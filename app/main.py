from __future__ import annotations

import sqlite3
from hashlib import sha256
import tempfile
import time
from contextlib import asynccontextmanager, closing
from datetime import datetime
from pathlib import Path

import markdown
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from starlette.background import BackgroundTask
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    build_discord_authorize_url,
    exchange_code_for_user,
    oauth_ready,
    upsert_discord_user,
    verify_oauth_state,
)
from .config import settings
from .database import SessionLocal, engine, get_db
from .migrate import require_current_schema
from .models import AuditLog, Category, Run, User, UserProfile, utcnow
from .notifications import notify_moderation
from .bio import render_bio
from .security import ensure_csrf_token, verify_csrf
from .turnstile import turnstile_enabled, verify_turnstile
from .utils import (
    format_time,
    ordinal,
    parse_time_to_ms,
    timeago,
    is_direct_video_url,
    validate_optional_url,
    validate_video_url,
    video_embed_url,
)

BASE_DIR = Path(__file__).resolve().parent
CSS_VERSION = sha256((BASE_DIR / "static" / "styles.css").read_bytes()).hexdigest()[:16]
RULES_DIR = BASE_DIR.parent / "rules"
PROFILE_COLORS = [
    {"value": "slate", "label": "Slate"},
    {"value": "blue", "label": "Blue"},
    {"value": "green", "label": "Green"},
    {"value": "purple", "label": "Purple"},
    {"value": "rust", "label": "Rust"},
    {"value": "teal", "label": "Teal"},
    {"value": "rose", "label": "Rose"},
    {"value": "olive", "label": "Olive"},
    {"value": "sand", "label": "Sand"},
    {"value": "charcoal", "label": "Charcoal"},
]
PROFILE_COLOR_VALUES = {color["value"] for color in PROFILE_COLORS}

BUILD_SEED = [
    {
        "slug": "july09",
        "name": "July 2009",
        "version": "852_0",
        "categories": [
            {
                "slug": "nme",
                "name": "No Major Exploits",
                "description": "",
                "rules_file": "rules/july09/nme.md",
                "legacy_slug": "2009-no-major-exploits",
            },
            {
                "slug": "oob-sla",
                "name": "Out-of-Bounds (SLA)",
                "description": "",
                "rules_file": "rules/july09/oob-sla.md",
                "legacy_slug": "2009-oob-sla",
            },
            {
                "slug": "inbounds",
                "name": "Inbounds (No SLA)",
                "description": "",
                "rules_file": "rules/july09/inbounds.md",
                "legacy_slug": "2009-in-bounds-no-sla",
            },
        ],
    },
    # {
    #     "slug": "feb10",
    #     "name": "February 2010",
    #     "version": "841_0",
    #     "categories": [
    #         {
    #             "slug": "nme",
    #             "name": "No Major Exploits",
    #             "description": "",
    #             "rules_file": "rules/feb10/nme.md",
    #         },
    #         {
    #             "slug": "oob-sla",
    #             "name": "Out-of-Bounds (SLA)",
    #             "description": "",
    #             "rules_file": "rules/feb10/oob-sla.md",
    #         },
    #         {
    #             "slug": "inbounds",
    #             "name": "Inbounds (No SLA)",
    #             "description": "",
    #             "rules_file": "rules/feb10/inbounds.md",
    #         },
    #     ],
    # },
]

def find_category_by_legacy_slug(db: Session, legacy_slug: str) -> Category | None:
    """Resolve an old `/category/{slug}` value to a category.

    Matches an explicit ``legacy_slug`` first, then falls back to the
    ``{build_slug}_{slug}`` convention used when ``legacy_slug`` is empty.
    """
    category = db.scalar(select(Category).where(Category.legacy_slug == legacy_slug))
    if category is not None:
        return category
    for candidate in db.scalars(select(Category)).all():
        if not candidate.legacy_slug and candidate.effective_legacy_slug == legacy_slug:
            return candidate
    return None


def seed_categories() -> None:
    with SessionLocal() as db:
        for build_order, build in enumerate(BUILD_SEED, start=1):
            for category_order, seed in enumerate(build["categories"], start=1):
                category = db.scalar(select(Category).where(Category.build_slug == build["slug"], Category.slug == seed["slug"]))
                if category is None:
                    category = Category(slug=seed["slug"])
                    db.add(category)
                category.name = seed["name"]
                category.description = seed.get("description", "")
                category.display_order = category_order
                category.build_slug = build["slug"]
                category.build_name = build["name"]
                category.build_version = build["version"]
                category.build_order = build_order
                category.rules_file = seed["rules_file"]
                if seed.get("legacy_slug"):
                    category.legacy_slug = seed["legacy_slug"]
        db.commit()

@asynccontextmanager
async def lifespan(_: FastAPI):
    require_current_schema(engine)
    seed_categories()
    yield

app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="p2runs_session",
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=settings.cookie_secure,
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["runtime"] = format_time
templates.env.filters["ordinal"] = ordinal
templates.env.filters["bio_markdown"] = render_bio
templates.env.filters["timeago"] = timeago

def render_template(name: str, context: dict, *, status_code: int = 200):
    """Render a Jinja template using Starlette's current request-first API."""
    return templates.TemplateResponse(
        request=context["request"],
        name=name,
        context=context,
        status_code=status_code,
    )

def get_current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None

    return db.get(User, int(user_id))

def is_owner(user: User | None) -> bool:
    return bool(user and user.discord_id in settings.owner_discord_ids)

def is_moderator(user: User | None) -> bool:
    return bool(user and (user.is_moderator or is_owner(user)))

def require_user(request: Request, db: Session) -> User:
    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in with Discord to continue.")

    return user

def require_moderator(request: Request, db: Session) -> User:
    user = require_user(request, db)
    if not is_moderator(user):
        raise HTTPException(status_code=403, detail="Moderator access required.")

    return user

def require_owner(request: Request, db: Session) -> User:
    user = require_user(request, db)
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Owner access required.")

    return user

def flash(request: Request, message: str, kind: str = "info") -> None:
    request.session["flash"] = {"message": message, "kind": kind}

def discord_avatar_url(user: User | None) -> str | None:
    if user is None:
        return None

    if user.avatar_hash:
        extension = "gif" if user.avatar_hash.startswith("a_") else "png"
        return (
            f"https://cdn.discordapp.com/avatars/{user.discord_id}/"
            f"{user.avatar_hash}.{extension}?size=64"
        )
    try:
        default_avatar = (int(user.discord_id) >> 22) % 6
    except ValueError:
        default_avatar = 0

    return f"https://cdn.discordapp.com/embed/avatars/{default_avatar}.png"

def template_context(request: Request, db: Session, **extra) -> dict:
    current_user = get_current_user(request, db)
    flash_message = request.session.pop("flash", None)

    return {
        "request": request,
        "app_name": settings.app_name,
        "css_version": CSS_VERSION,
        "current_user": current_user,
        "current_user_avatar_url": discord_avatar_url(current_user),
        "is_owner": is_owner(current_user),
        "is_moderator": is_moderator(current_user),
        "csrf_token": ensure_csrf_token(request),
        "oauth_ready": oauth_ready(),
        "turnstile_enabled": turnstile_enabled(),
        "turnstile_site_key": settings.turnstile_site_key,
        "flash": flash_message,
        **extra,
    }

def best_approved_runs(
    db: Session, category_id: int, limit: int | None = None, *, show_obsolete: bool = False
) -> list[Run]:
    runs = db.scalars(
        select(Run)
        .options(joinedload(Run.runner), joinedload(Run.category))
        .where(Run.category_id == category_id, Run.status == "approved")
        .order_by(Run.time_ms.asc(), Run.submitted_at.asc(), Run.id.asc())
    ).all()

    best: list[Run] = []
    seen_users: set[int] = set()
    for run in runs:
        if run.user_id in seen_users and not show_obsolete:
            continue
        seen_users.add(run.user_id)
        best.append(run)
        if limit is not None and len(best) >= limit:
            break
    return best

def audit_run_event(
    db: Session,
    action: str,
    actor: User,
    run: Run,
    *,
    runner: User | None = None,
    category: Category | None = None,
    details: str = "",
) -> None:
    """Store immutable display snapshots so deleted runs remain auditable."""
    runner = runner or run.runner
    category = category or run.category

    db.add(
        AuditLog(
            action=action,
            run_id=run.id,
            actor_discord_id=actor.discord_id,
            actor_name=actor.display_name,
            runner_discord_id=runner.discord_id,
            runner_name=runner.display_name,
            category_name=f"{category.build_name} · {category.name}",
            time_ms=run.time_ms,
            details=details,
        )
    )

def list_moderators(db: Session) -> list[dict]:
    """Moderators (including owners) with avatar URLs for templates."""
    users = db.scalars(
        select(User).where(
            User.is_moderator.is_(True) | (User.discord_id.in_(settings.owner_discord_ids))
        ).order_by(User.username.asc())
    ).all()
    return [{"user": user, "avatar_url": discord_avatar_url(user)} for user in users]

def build_categories_for(db: Session, build_slug: str) -> list[Category]:
    return db.scalars(
        select(Category)
        .where(Category.build_slug == build_slug)
        .order_by(Category.display_order, Category.id)
    ).all()

def latest_runs_for(db: Session, categories: list[Category], limit: int = 10) -> list[Run]:
    category_ids = [category.id for category in categories]
    if not category_ids:
        return []
    return db.scalars(
        select(Run)
        .options(joinedload(Run.runner), joinedload(Run.category))
        .where(Run.category_id.in_(category_ids), Run.status == "approved")
        .order_by(Run.submitted_at.desc(), Run.id.desc())
        .limit(limit)
    ).all()

@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    if len(BUILD_SEED) == 1:
        return RedirectResponse(f"/{BUILD_SEED[0]['slug']}", status_code=status.HTTP_302_FOUND)

    categories = db.scalars(
        select(Category).order_by(Category.build_order, Category.display_order)
    ).all()

    builds = []
    for build in BUILD_SEED:
        build_categories = [category for category in categories if category.build_slug == build["slug"]]
        builds.append(
            {
                **build,
                "categories": build_categories,
            }
        )

    return render_template(
        "home.html",
        template_context(request, db, builds=builds),
    )

@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request, db: Session = Depends(get_db)):
    return render_template(
        "about.html",
        template_context(
            request,
            db,
            moderators=list_moderators(db),
        ),
    )

@app.get("/category/{slug}", response_class=HTMLResponse)
def category_legacy_redirect(slug: str, request: Request, db: Session = Depends(get_db)):
    """Redirect old `/category/{slug}` URLs to `/{build}/{category}`."""
    category = find_category_by_legacy_slug(db, slug)
    if category is None:
        # Also accept the current slug when the build is unambiguous, so very
        # old links like `/category/nme` keep working if possible.
        matches = db.scalars(select(Category).where(Category.slug == slug)).all()
        if len(matches) == 1:
            category = matches[0]
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found.")
    destination = f"/{category.build_slug}/{category.slug}"
    if request.query_params:
        destination += f"?{request.query_params}"
    return RedirectResponse(destination, status_code=status.HTTP_301_MOVED_PERMANENTLY)


@app.get("/category/{slug}/rules", response_class=HTMLResponse)
def category_legacy_rules_redirect(slug: str, request: Request, db: Session = Depends(get_db)):
    category = find_category_by_legacy_slug(db, slug)
    if category is None:
        matches = db.scalars(select(Category).where(Category.slug == slug)).all()
        if len(matches) == 1:
            category = matches[0]
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found.")
    destination = f"/{category.build_slug}/{category.slug}/rules"
    if request.query_params:
        destination += f"?{request.query_params}"
    return RedirectResponse(destination, status_code=status.HTTP_301_MOVED_PERMANENTLY)

@app.get("/auth/login")
def login(request: Request):
    return RedirectResponse(build_discord_authorize_url(request), status_code=status.HTTP_302_FOUND)

@app.get("/auth/callback")
async def auth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if error:
        raise HTTPException(status_code=400, detail="Discord login was cancelled or denied.")
    if not code:
        raise HTTPException(status_code=400, detail="Discord did not return an authorization code.")

    verify_oauth_state(request, state)
    profile = await exchange_code_for_user(code)
    discord_id = str(profile.get("id", "")).strip()
    username = str(profile.get("username", "")).strip()
    if not discord_id or not username:
        raise HTTPException(status_code=502, detail="Discord returned an incomplete user profile.")

    existing_user = db.scalar(select(User).where(User.discord_id == discord_id))
    needs_signup_check = existing_user is None or existing_user.last_login_at is None
    if needs_signup_check and turnstile_enabled():
        request.session.pop("user_id", None)
        request.session["pending_discord_signup"] = {
            "id": discord_id,
            "username": username,
            "global_name": profile.get("global_name"),
            "avatar": profile.get("avatar"),
        }
        request.session["pending_discord_signup_started_at"] = int(time.time())
        return RedirectResponse("/auth/verify", status_code=status.HTTP_303_SEE_OTHER)

    user = upsert_discord_user(db, profile)
    request.session["user_id"] = user.id
    request.session.pop("csrf_token", None)
    flash(request, f"Signed in as {user.display_name}.", "success")
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


def pending_discord_signup(request: Request) -> dict:
    profile = request.session.get("pending_discord_signup")
    started_at = request.session.get("pending_discord_signup_started_at")

    if not isinstance(profile, dict) or not isinstance(started_at, int):
        raise HTTPException(status_code=400, detail="No Discord sign-up is pending.")

    if int(time.time()) - started_at > 10 * 60:
        request.session.pop("pending_discord_signup", None)
        request.session.pop("pending_discord_signup_started_at", None)
        raise HTTPException(status_code=400, detail="Discord sign-up expired. Please start again.")

    return profile

@app.get("/auth/verify", response_class=HTMLResponse)
def signup_verification(request: Request, db: Session = Depends(get_db)):
    pending_discord_signup(request)
    return render_template(
        "auth_verify.html",
        template_context(request, db, errors=[]),
    )

@app.post("/auth/verify", response_class=HTMLResponse)
def complete_signup_verification(
    request: Request,
    csrf_token: str = Form(...),
    cf_turnstile_response: str = Form("", alias="cf-turnstile-response"),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)

    profile = pending_discord_signup(request)
    if not verify_turnstile(cf_turnstile_response, "discord_signup"):
        return render_template(
            "auth_verify.html",
            template_context(
                request,
                db,
                errors=["Complete the CAPTCHA and try again."],
            ),
            status_code=422,
        )

    user = upsert_discord_user(db, profile)
    request.session.pop("pending_discord_signup", None)
    request.session.pop("pending_discord_signup_started_at", None)
    request.session["user_id"] = user.id
    request.session.pop("csrf_token", None)

    flash(request, f"Sign-up complete. Signed in as {user.display_name}.", "success")
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/auth/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    verify_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/submit", response_class=HTMLResponse)
def submit_form(request: Request, db: Session = Depends(get_db)):
    require_user(request, db)

    categories = db.scalars(
        select(Category).order_by(Category.build_order, Category.display_order)
    ).all()

    return render_template(
        "submit.html",
        template_context(request, db, categories=categories, values={}, errors=[]),
    )

@app.post("/submit", response_class=HTMLResponse)
def submit_run(
    request: Request,
    background_tasks: BackgroundTasks,
    category_id: int = Form(...),
    run_time: str = Form(...),
    video_url: str = Form(...),
    splits_url: str = Form(""),
    notes: str = Form(""),
    csrf_token: str = Form(...),
    cf_turnstile_response: str = Form("", alias="cf-turnstile-response"),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)

    user = require_user(request, db)
    categories = db.scalars(
        select(Category).order_by(Category.build_order, Category.display_order)
    ).all()

    category = db.get(Category, category_id)
    errors: list[str] = []

    if category is None:
        errors.append("Choose a valid category.")

    try:
        time_ms = parse_time_to_ms(run_time)
    except ValueError as exc:
        time_ms = 0
        errors.append(str(exc))

    try:
        clean_video_url = validate_video_url(video_url)
    except ValueError as exc:
        clean_video_url = video_url.strip()
        errors.append(str(exc))

    try:
        clean_splits_url = validate_optional_url(splits_url, "Splits URL")
    except ValueError as exc:
        clean_splits_url = splits_url.strip()
        errors.append(str(exc))

    clean_notes = notes.strip()
    if len(clean_notes) > 2000:
        errors.append("Notes must be 2,000 characters or fewer.")
    if not errors and not verify_turnstile(cf_turnstile_response, "submit_run"):
        errors.append("Complete the CAPTCHA and try again.")

    if errors:
        return render_template(
            "submit.html",
            template_context(
                request,
                db,
                categories=categories,
                values={
                    "category_id": category_id,
                    "run_time": run_time,
                    "video_url": video_url,
                    "splits_url": splits_url,
                    "notes": notes,
                },
                errors=errors,
            ),
            status_code=422,
        )

    run = Run(
        user_id=user.id,
        category_id=category_id,
        time_ms=time_ms,
        video_url=clean_video_url,
        splits_url=clean_splits_url,
        notes=clean_notes,
        status="pending",
    )
    db.add(run)
    db.flush()

    audit_run_event(db, "submitted", user, run, runner=user, category=category)
    db.commit()
    db.refresh(run)

    flash(request, "Run submitted for moderator review.", "success")
    background_tasks.add_task(
        notify_moderation, run.id, user.display_name,
        f"{category.build_name} · {category.name}", run.time_ms,
    )

    return RedirectResponse(f"/runs/{run.id}", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/me", response_class=HTMLResponse)
def own_profile_redirect(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    return RedirectResponse(f"/users/{user.discord_id}", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/users/{discord_id}", response_class=HTMLResponse)
def user_profile(discord_id: str, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(
        select(User)
        .options(joinedload(User.profile))
        .where(User.discord_id == discord_id)
    )

    if user is None:
        raise HTTPException(status_code=404, detail="Runner not found.")

    current_user = get_current_user(request, db)
    own_profile = bool(current_user and current_user.id == user.id)
    run_query = select(Run).options(joinedload(Run.category)).where(Run.user_id == user.id)

    if not own_profile:
        run_query = run_query.where(Run.status == "approved")

    runs = db.scalars(
        run_query.order_by(Run.submitted_at.desc())
    ).all()

    color = user.profile.background_color if user.profile else "slate"
    if color not in PROFILE_COLOR_VALUES:
        color = "slate"

    return render_template(
        "user_profile.html",
        template_context(
            request,
            db,
            profile_user=user,
            profile=user.profile,
            profile_color=color,
            profile_avatar_url=discord_avatar_url(user),
            profile_is_moderator=is_moderator(user),
            own_profile=own_profile,
            runs=runs,
        ),
    )

@app.get("/me/profile", response_class=HTMLResponse)
def edit_own_profile_form(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    profile = db.get(UserProfile, user.id)

    return render_template(
        "edit_profile.html",
        template_context(
            request,
            db,
            profile=profile,
            colors=PROFILE_COLORS,
            values={
                "bio": profile.bio if profile else "",
                "background_color": profile.background_color if profile else "slate",
            },
            errors=[],
        ),
    )

@app.post("/me/profile", response_class=HTMLResponse)
def edit_own_profile(
    request: Request,
    bio: str = Form(""),
    background_color: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)

    user = require_user(request, db)
    clean_bio = bio.strip()
    errors: list[str] = []

    if len(clean_bio) > 500:
        errors.append("Bio must be 500 characters or fewer.")
    if background_color not in PROFILE_COLOR_VALUES:
        errors.append("Choose a valid background color.")
    if errors:
        return render_template(
            "edit_profile.html",
            template_context(
                request,
                db,
                profile=db.get(UserProfile, user.id),
                colors=PROFILE_COLORS,
                values={"bio": bio, "background_color": background_color},
                errors=errors,
            ),
            status_code=422,
        )

    profile = db.get(UserProfile, user.id)
    if profile is None:
        profile = UserProfile(user_id=user.id)
        db.add(profile)

    profile.bio = clean_bio
    profile.background_color = background_color
    db.commit()

    flash(request, "Profile updated.", "success")
    return RedirectResponse(f"/users/{user.discord_id}", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(run_id: int, request: Request, db: Session = Depends(get_db)):
    run = db.scalar(
        select(Run)
        .options(joinedload(Run.runner), joinedload(Run.category), joinedload(Run.reviewed_by))
        .where(Run.id == run_id)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    current_user = get_current_user(request, db)
    if run.status != "approved":
        allowed = current_user and (current_user.id == run.user_id or is_moderator(current_user))
        if not allowed:
            raise HTTPException(status_code=404, detail="Run not found.")

    if run.status == "approved":
        rank_label = "Obsoleted"
        seen_users: set[int] = set()
        for place, candidate in enumerate(best_approved_runs(db, run.category_id), start=1):
            if candidate.user_id in seen_users:
                continue
            seen_users.add(candidate.user_id)
            if candidate.id == run.id:
                rank_label = ordinal(place)
                break
    elif run.status == "pending":
        rank_label = "Pending review"
    else:
        rank_label = "—"

    build_categories = build_categories_for(db, run.category.build_slug)
    embed_parent = request.url.hostname or "localhost"

    return render_template(
        "run_detail.html",
        template_context(
            request,
            db,
            run=run,
            rank_label=rank_label,
            embed_url=video_embed_url(run.video_url, parent=embed_parent) if run.video_url else None,
            direct_video_url=run.video_url if run.video_url and is_direct_video_url(run.video_url) else None,
            latest_runs=latest_runs_for(db, build_categories),
            moderators=list_moderators(db),
        ),
    )

@app.post("/runs/{run_id}/delete")
def delete_run(
    run_id: int,
    request: Request,
    csrf_token: str = Form(...),
    return_to: str = Form("category"),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)

    moderator = require_moderator(request, db)
    run = db.scalar(
        select(Run)
        .options(joinedload(Run.category))
        .where(Run.id == run_id)
    )

    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    deleted_run_id = run.id
    audit_run_event(
        db,
        "deleted",
        moderator,
        run,
        details=f"Previous status: {run.status}.",
    )
    db.delete(run)
    db.commit()

    flash(request, f"Run #{deleted_run_id} deleted.", "success")
    destination = "/moderation" if return_to == "moderation" else f"/{run.category.build_slug}/{run.category.slug}"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)

@app.get("/moderation", response_class=HTMLResponse)
def moderation(request: Request, db: Session = Depends(get_db)):
    require_moderator(request, db)
    pending_runs = db.scalars(
        select(Run)
        .options(joinedload(Run.runner), joinedload(Run.category))
        .where(Run.status == "pending")
        .order_by(Run.submitted_at.asc())
    ).all()

    return render_template(
        "moderation.html",
        template_context(request, db, pending_runs=pending_runs),
    )

@app.get("/moderation/runs/add", response_class=HTMLResponse)
def add_run_as_moderator_form(request: Request, db: Session = Depends(get_db)):
    require_moderator(request, db)
    categories = db.scalars(
        select(Category).order_by(Category.build_order, Category.display_order)
    ).all()
    return render_template(
        "moderation_add_run.html",
        template_context(request, db, categories=categories, values={}, errors=[]),
    )

@app.get("/moderation/runs/{run_id}/edit", response_class=HTMLResponse)
def edit_run_as_moderator_form(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    require_moderator(request, db)
    run = db.scalar(
        select(Run)
        .options(joinedload(Run.runner), joinedload(Run.category))
        .where(Run.id == run_id)
    )

    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    categories = db.scalars(
        select(Category).order_by(Category.build_order, Category.display_order)
    ).all()

    return render_template(
        "moderation_edit_run.html",
        template_context(
            request,
            db,
            run=run,
            categories=categories,
            values={
                "discord_id": run.runner.discord_id,
                "temporary_display_name": run.runner.display_name,
                "category_id": run.category_id,
                "run_time": format_time(run.time_ms),
                "video_url": run.video_url,
                "splits_url": run.splits_url,
                "notes": run.notes,
            },
            errors=[],
        ),
    )


@app.post("/moderation/runs/{run_id}/edit", response_class=HTMLResponse)
def edit_run_as_moderator(
    run_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    discord_id: str = Form(...),
    temporary_display_name: str = Form(...),
    category_id: int = Form(...),
    run_time: str = Form(...),
    video_url: str = Form(...),
    splits_url: str = Form(""),
    notes: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)

    moderator = require_moderator(request, db)
    run = db.scalar(
        select(Run)
        .options(joinedload(Run.runner), joinedload(Run.category))
        .where(Run.id == run_id)
    )

    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    clean_discord_id = discord_id.strip()
    clean_display_name = temporary_display_name.strip()
    clean_notes = notes.strip()
    category = db.get(Category, category_id)
    errors: list[str] = []

    if not clean_discord_id.isdigit() or not 15 <= len(clean_discord_id) <= 25:
        errors.append("Enter a valid numeric Discord user ID.")
    if not clean_display_name:
        errors.append("Enter a temporary display name.")
    elif len(clean_display_name) > 80:
        errors.append("Temporary display name must be 80 characters or fewer.")
    if category is None:
        errors.append("Choose a valid category.")

    try:
        time_ms = parse_time_to_ms(run_time)
    except ValueError as exc:
        time_ms = 0
        errors.append(str(exc))

    try:
        clean_video_url = validate_video_url(video_url)
    except ValueError as exc:
        clean_video_url = video_url.strip()
        errors.append(str(exc))

    try:
        clean_splits_url = validate_optional_url(splits_url, "Splits URL")
    except ValueError as exc:
        clean_splits_url = splits_url.strip()
        errors.append(str(exc))

    if len(clean_notes) > 2000:
        errors.append("Notes must be 2,000 characters or fewer.")

    values = {
        "discord_id": discord_id,
        "temporary_display_name": temporary_display_name,
        "category_id": category_id,
        "run_time": run_time,
        "video_url": video_url,
        "splits_url": splits_url,
        "notes": notes,
    }

    if errors:
        categories = db.scalars(
            select(Category).order_by(Category.build_order, Category.display_order)
        ).all()
        return render_template(
            "moderation_edit_run.html",
            template_context(
                request,
                db,
                run=run,
                categories=categories,
                values=values,
                errors=errors,
            ),
            status_code=422,
        )

    old_runner = run.runner
    old_category = run.category
    old_runner_name = old_runner.display_name
    old_runner_discord_id = old_runner.discord_id
    old_time_ms = run.time_ms
    old_video_url = run.video_url
    old_splits_url = run.splits_url
    old_notes = run.notes

    runner = db.scalar(select(User).where(User.discord_id == clean_discord_id))
    if runner is None:
        runner = User(discord_id=clean_discord_id, username=clean_display_name)
        db.add(runner)
        db.flush()
    elif runner.last_login_at is None:
        runner.username = clean_display_name

    changes: list[str] = []
    if old_runner_discord_id != runner.discord_id:
        changes.append(
            f"Runner: {old_runner_name} ({old_runner_discord_id}) to "
            f"{runner.display_name} ({runner.discord_id})."
        )
    elif old_runner_name != runner.display_name:
        changes.append(f"Temporary runner name: {old_runner_name} to {runner.display_name}.")
    if old_category.id != category.id:
        changes.append(
            f"Category: {old_category.build_name} · {old_category.name} to "
            f"{category.build_name} · {category.name}."
        )
    if old_time_ms != time_ms:
        changes.append(f"Time: {format_time(old_time_ms)} to {format_time(time_ms)}.")
    if old_video_url != clean_video_url:
        changes.append("Video URL changed.")
    if old_splits_url != clean_splits_url:
        changes.append("Splits URL changed.")
    if old_notes != clean_notes:
        changes.append("Notes changed.")

    run.user_id = runner.id
    run.category_id = category.id
    run.time_ms = time_ms
    run.video_url = clean_video_url
    run.splits_url = clean_splits_url
    run.notes = clean_notes

    if changes:
        audit_run_event(
            db,
            "edited",
            moderator,
            run,
            runner=runner,
            category=category,
            details=" ".join(changes),
        )
        flash(request, f"Run #{run.id} updated.", "success")
    else:
        flash(request, f"No changes made to run #{run.id}.", "info")
    db.commit()

    if changes and run.status == "pending":
        background_tasks.add_task(
            notify_moderation, run.id, runner.display_name,
            f"{category.build_name} · {category.name}", run.time_ms, True,
        )

    return RedirectResponse(f"/runs/{run.id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/moderation/runs/add", response_class=HTMLResponse)
def add_run_as_moderator(
    request: Request,
    discord_id: str = Form(...),
    temporary_display_name: str = Form(...),
    category_id: int = Form(...),
    run_time: str = Form(...),
    video_url: str = Form(...),
    splits_url: str = Form(""),
    notes: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)

    moderator = require_moderator(request, db)
    clean_discord_id = discord_id.strip()
    clean_display_name = temporary_display_name.strip()
    clean_notes = notes.strip()
    category = db.get(Category, category_id)
    errors: list[str] = []

    if not clean_discord_id.isdigit() or not 15 <= len(clean_discord_id) <= 25:
        errors.append("Enter a valid numeric Discord user ID.")
    if not clean_display_name:
        errors.append("Enter a temporary display name.")
    elif len(clean_display_name) > 80:
        errors.append("Temporary display name must be 80 characters or fewer.")
    if category is None:
        errors.append("Choose a valid category.")

    try:
        time_ms = parse_time_to_ms(run_time)
    except ValueError as exc:
        time_ms = 0
        errors.append(str(exc))

    try:
        clean_video_url = validate_video_url(video_url)
    except ValueError as exc:
        clean_video_url = video_url.strip()
        errors.append(str(exc))

    try:
        clean_splits_url = validate_optional_url(splits_url, "Splits URL")
    except ValueError as exc:
        clean_splits_url = splits_url.strip()
        errors.append(str(exc))

    if len(clean_notes) > 2000:
        errors.append("Notes must be 2,000 characters or fewer.")

    if errors:
        categories = db.scalars(
            select(Category).order_by(Category.build_order, Category.display_order)
        ).all()
        return render_template(
            "moderation_add_run.html",
            template_context(
                request,
                db,
                categories=categories,
                values={
                    "discord_id": discord_id,
                    "temporary_display_name": temporary_display_name,
                    "category_id": category_id,
                    "run_time": run_time,
                    "video_url": video_url,
                    "splits_url": splits_url,
                    "notes": notes,
                },
                errors=errors,
            ),
            status_code=422,
        )

    runner = db.scalar(select(User).where(User.discord_id == clean_discord_id))
    if runner is None:
        runner = User(discord_id=clean_discord_id, username=clean_display_name)
        db.add(runner)
        db.flush()
    elif runner.last_login_at is None:
        runner.username = clean_display_name

    run = Run(
        user_id=runner.id,
        category_id=category_id,
        time_ms=time_ms,
        video_url=clean_video_url,
        splits_url=clean_splits_url,
        notes=clean_notes,
        status="approved",
        reviewed_at=utcnow(),
        reviewed_by_user_id=moderator.id,
    )
    db.add(run)
    db.flush()

    audit_run_event(
        db,
        "added_manually",
        moderator,
        run,
        runner=runner,
        category=category,
        details="Approved when added manually.",
    )
    db.commit()
    db.refresh(run)
    flash(request, f"Run #{run.id} added for {runner.display_name}.", "success")

    return RedirectResponse(f"/runs/{run.id}", status_code=status.HTTP_303_SEE_OTHER)

def _get_pending_run(db: Session, run_id: int) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.status != "pending":
        raise HTTPException(status_code=409, detail="This run has already been reviewed.")
    return run

@app.post("/moderation/{run_id}/approve")
def approve_run(
    run_id: int,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)

    moderator = require_moderator(request, db)
    run = _get_pending_run(db, run_id)
    run.status = "approved"
    run.reviewed_at = utcnow()
    run.reviewed_by_user_id = moderator.id
    run.rejection_reason = None

    audit_run_event(db, "approved", moderator, run)
    db.commit()

    flash(request, f"Run #{run.id} approved.", "success")
    return RedirectResponse("/moderation", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/moderation/{run_id}/reject")
def reject_run(
    run_id: int,
    request: Request,
    rejection_reason: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)

    moderator = require_moderator(request, db)
    reason = rejection_reason.strip()
    if len(reason) > 500:
        raise HTTPException(status_code=422, detail="Rejection reason must be 500 characters or fewer.")

    run = _get_pending_run(db, run_id)
    run.status = "rejected"
    run.reviewed_at = utcnow()
    run.reviewed_by_user_id = moderator.id
    run.rejection_reason = reason or "No reason provided."

    audit_run_event(db, "rejected", moderator, run, details=run.rejection_reason)
    db.commit()

    flash(request, f"Run #{run.id} rejected.", "info")
    return RedirectResponse("/moderation", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/owner/mods", response_class=HTMLResponse)
def owner_mods(request: Request, db: Session = Depends(get_db)):
    require_owner(request, db)
    users = db.scalars(select(User).order_by(User.is_moderator.desc(), User.username.asc())).all()
    return render_template(
        "owner_mods.html",
        template_context(request, db, users=users, owner_discord_ids=settings.owner_discord_ids),
    )

@app.get("/owner/audit-log", response_class=HTMLResponse)
def owner_audit_log(request: Request, db: Session = Depends(get_db)):
    require_owner(request, db)
    entries = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())).all()
    return render_template(
        "owner_audit_log.html",
        template_context(request, db, entries=entries),
    )

@app.post("/owner/database/download")
def download_database_snapshot(
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)

    require_owner(request, db)
    if engine.url.get_backend_name() != "sqlite":
        raise HTTPException(status_code=501, detail="Database snapshots require SQLite.")

    database_path_value = engine.url.database
    if not database_path_value or database_path_value == ":memory:":
        raise HTTPException(status_code=503, detail="The SQLite database is not stored on disk.")

    database_path = Path(database_path_value).resolve()
    if not database_path.is_file():
        raise HTTPException(status_code=503, detail="The SQLite database file was not found.")

    temporary_file = tempfile.NamedTemporaryFile(
        prefix="portal2-runs-",
        suffix=".db",
        delete=False,
    )
    snapshot_path = Path(temporary_file.name)
    temporary_file.close()

    try:
        with closing(sqlite3.connect(database_path)) as source, closing(
            sqlite3.connect(snapshot_path)
        ) as destination:
            source.backup(destination)
    except sqlite3.Error as exc:
        snapshot_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Could not create a database snapshot.") from exc

    filename = f"portal2-runs-{utcnow().strftime('%Y%m%d-%H%M%S')}.db"
    return FileResponse(
        snapshot_path,
        media_type="application/vnd.sqlite3",
        filename=filename,
        background=BackgroundTask(snapshot_path.unlink, missing_ok=True),
    )

@app.post("/owner/mods/add")
def add_mod(
    request: Request,
    discord_id: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)

    require_owner(request, db)

    discord_id = discord_id.strip()
    if not discord_id.isdigit() or not 15 <= len(discord_id) <= 25:
        raise HTTPException(status_code=422, detail="Enter a valid numeric Discord user ID.")

    if discord_id in settings.owner_discord_ids:
        flash(request, "The owner already has moderator access.", "info")
        return RedirectResponse("/owner/mods", status_code=status.HTTP_303_SEE_OTHER)

    user = db.scalar(select(User).where(User.discord_id == discord_id))
    if user is None:
        user = User(discord_id=discord_id, username=f"Discord user {discord_id}", is_moderator=True)
        db.add(user)
    else:
        user.is_moderator = True

    db.commit()
    flash(request, f"Moderator access enabled for Discord ID {discord_id}.", "success")

    return RedirectResponse("/owner/mods", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/owner/mods/{user_id}/remove")
def remove_mod(
    user_id: int,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)

    require_owner(request, db)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.discord_id in settings.owner_discord_ids:
        raise HTTPException(status_code=400, detail="The owner cannot be demoted.")
    user.is_moderator = False
    db.commit()

    flash(request, f"Moderator access removed from {user.display_name}.", "success")
    return RedirectResponse("/owner/mods", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/{build_slug}", response_class=HTMLResponse)
def build_page(build_slug: str, request: Request, db: Session = Depends(get_db)):
    """Redirect a build to its first category leaderboard.

    Defined last so specific single-segment routes like /about, /submit,
    and /me match first.
    """
    if not any(build["slug"] == build_slug for build in BUILD_SEED):
        raise HTTPException(status_code=404, detail="Build not found.")
    first = db.scalar(
        select(Category)
        .where(Category.build_slug == build_slug)
        .order_by(Category.display_order, Category.id)
    )
    if first is None:
        raise HTTPException(status_code=404, detail="Build has no categories yet.")
    return RedirectResponse(
        f"/{build_slug}/{first.slug}", status_code=status.HTTP_302_FOUND
    )

@app.get("/{build_slug}/{category_slug}", response_class=HTMLResponse)
def category_page(
    build_slug: str,
    category_slug: str,
    request: Request,
    show_obsolete: bool = False,
    db: Session = Depends(get_db),
):
    # Defined last so specific routes like /users/{id} and /runs/{id} match first.
    category = db.scalar(
        select(Category).where(
            Category.build_slug == build_slug, Category.slug == category_slug
        )
    )
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found.")

    runs = best_approved_runs(db, category.id, show_obsolete=show_obsolete)
    places = {}
    seen_users = set()

    for run in runs:
        if run.user_id not in seen_users:
            seen_users.add(run.user_id)
            places[run.id] = len(seen_users)

    build_categories = build_categories_for(db, category.build_slug)

    return render_template(
        "category.html",
        template_context(
            request, db, category=category, runs=runs,
            places=places, show_obsolete=show_obsolete,
            build_categories=build_categories,
            latest_runs=latest_runs_for(db, build_categories),
            moderators=list_moderators(db),
        ),
    )

@app.get("/{build_slug}/{category_slug}/rules", response_class=HTMLResponse)
def category_rules(
    build_slug: str, category_slug: str, request: Request, db: Session = Depends(get_db)
):
    category = db.scalar(
        select(Category).where(
            Category.build_slug == build_slug, Category.slug == category_slug
        )
    )
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found.")

    rules_path = (BASE_DIR.parent / category.rules_file).resolve()
    rules_root = RULES_DIR.resolve()
    if rules_root not in rules_path.parents or not rules_path.is_file():
        raise HTTPException(status_code=404, detail="Rules not found.")

    rules_html = Markup(
        markdown.markdown(
            rules_path.read_text(encoding="utf-8"),
            extensions=["extra", "sane_lists"],
        )
    )
    return render_template(
        "category_rules.html",
        template_context(request, db, category=category, rules_html=rules_html),
    )


@app.exception_handler(HTTPException)
def http_exception_handler(request: Request, exc: HTTPException):
    with SessionLocal() as db:
        return render_template(
            "error.html",
            template_context(request, db, status_code=exc.status_code, detail=exc.detail),
            status_code=exc.status_code,
        )
