"""Deploy LearnHub to a free Hugging Face Docker Space (2 vCPU, 16 GB RAM, no card needed).

One-time login (hidden prompt; answer "n" to the git-credential question):
    python -c "from huggingface_hub import login; login()"      (use a token with Write access)
Deploy / redeploy:
    python scripts/deploy_hf.py [--name learnhub] [--forward-secrets]

--forward-secrets copies ANTHROPIC_API_KEY, DATABASE_URL, SMTP_* and GOOGLE_* from your local .env into the Space's
private secrets (names are printed, values never are). Without it nothing from .env leaves your machine."""
import argparse
import os
import secrets
import shutil
import sys
import tempfile

from huggingface_hub import HfApi

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(ROOT, "data")
SKIP = shutil.ignore_patterns(".git", ".github", ".env", "data", "tests", "__pycache__", "*.pyc", "INTERVIEW_PREP.md")
FORWARD = ("ANTHROPIC_API_KEY", "DATABASE_URL", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
           "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "SENTRY_DSN", "LEARNHUB_MODEL", "LEARNHUB_TUTOR_MODEL")
CARD = """---
title: LearnHub
emoji: 🎓
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 8100
short_description: AI-powered adaptive learning platform
---

"""


def read_env():
    out, path = {}, os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="learnhub")
    ap.add_argument("--forward-secrets", action="store_true")
    ap.add_argument("--private", action="store_true", help="make the Space private (default: public)")
    args = ap.parse_args()

    api = HfApi()
    try:
        me = api.whoami()["name"]
    except Exception:
        sys.exit("Not logged in to Hugging Face. Run once:\n  python -c \"from huggingface_hub import login; login()\"")
    repo = f"{me}/{args.name}"
    api.create_repo(repo, repo_type="space", space_sdk="docker", private=args.private, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:  # clean copy of the project with the Space's README front matter
        stage = os.path.join(tmp, "src")
        shutil.copytree(ROOT, stage, ignore=SKIP)
        readme = os.path.join(stage, "README.md")
        text = open(readme, encoding="utf-8").read()
        with open(readme, "w", encoding="utf-8", newline="\n") as f:
            f.write(CARD + text)
        api.upload_folder(folder_path=stage, repo_id=repo, repo_type="space", commit_message="Deploy LearnHub")

    host = api.space_info(repo).host or f"https://{me.lower()}-{args.name}.hf.space"
    for k, v in {"ENVIRONMENT": "production", "WORKER_MODE": "inline", "APP_URL": host}.items():
        api.add_space_variable(repo, k, v)

    os.makedirs(DATA, exist_ok=True)
    code_file, marker = os.path.join(DATA, "hf_signup_code.txt"), os.path.join(DATA, "hf_secrets_set")
    if not os.path.exists(marker):  # first deploy only: rotating SECRET_KEY would sign everyone out
        api.add_space_secret(repo, "SECRET_KEY", secrets.token_urlsafe(48))
        code = secrets.token_urlsafe(12)
        open(code_file, "w").write(code)
        api.add_space_secret(repo, "SIGNUP_CODE", code)
        open(marker, "w").write("1")
    sent = []
    if args.forward_secrets:
        env = read_env()
        for k in FORWARD:
            if env.get(k):
                api.add_space_secret(repo, k, env[k])
                sent.append(k)

    print(f"\nSpace:   https://huggingface.co/spaces/{repo}   (build logs are on that page)")
    print(f"Live at: {host}   (the first build takes several minutes)")
    print(f"Sign-up invite code saved to: {os.path.abspath(code_file)}")
    print("Secrets forwarded from .env: " + (", ".join(sent) if sent else "none (re-run with --forward-secrets to send your API key)"))


if __name__ == "__main__":
    main()
