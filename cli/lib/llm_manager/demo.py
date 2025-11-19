"""
demo.py — manual POC shaker for llm_manager

Usage examples:

  # set basics
  export RDST_LLM_SHARED_KEY='ALPHA-STATIC-SHARED-KEY'
  export OPENAI_API_KEY='sk-...'
  export ANTHROPIC_API_KEY='...'
  export GEMINI_API_KEY='AIza...'

  # run the full quick suite (safe, no destructive ops)
  python -m llm_manager.demo --suite quick

  # save encrypted keys for both providers (writes ~/.rdst/keys/*.key.enc)
  python -m llm_manager.demo --save-keys

  # target a single provider smoke test
  python -m llm_manager.demo --smoke openai
  python -m llm_manager.demo --smoke claude
  python -m llm_manager.demo --smoke gemini
  python -m llm_manager.demo --smoke lmstudio

  # run parameter normalization checks
  python -m llm_manager.demo --test-stop --test-overrides

  # run error-surface checks
  python -m llm_manager.demo --test-errors

  # run tiny rdst shim
  python -m llm_manager.demo --shim-explain "SELECT * FROM orders o JOIN users u ON u.id=o.user_id ORDER BY o.created_at DESC;"

  # use LM Studio for SQL explain
  python -m llm_manager.demo --provider lmstudio --shim-explain "SELECT * FROM orders WHERE user_id = 123"

  # test stop sequences with LM Studio
  python -m llm_manager.demo --test-stop --provider lmstudio

  # test overrides with LM Studio
  python -m llm_manager.demo --test-overrides --provider lmstudio
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional, Dict

from cli.rdst_cli import RdstResult
from llm_manager import LLMManager
from llm_manager.base import LLMError


def _require_env(name: str) -> str:
    v = os.getenv(name, "")
    if not v:
        raise SystemExit(f"Missing required environment variable: {name}")
    return v

def _key_paths() -> Dict[str, Path]:
    keys_dir = Path(os.getenv("RDST_LLM_KEYS_DIR", Path.home() / ".rdst" / "keys"))
    return {
        "openai": keys_dir / "openai.key.enc",
        "claude": keys_dir / "claude.key.enc",
        "gemini": keys_dir / "gemini.key.enc",
        "lmstudio": keys_dir / "lmstudio.key.enc",  # Not needed but here for consistency
    }

def sanity_check_imports() -> None:
    print("step 1) sanity: imports & paths")
    here = Path(__file__).resolve().parent
    print("ok: imports fine. script dir =", here)

def save_encrypted_keys() -> None:
    print("step 2/3) encrypt & store keys")
    shared = _require_env("RDST_LLM_SHARED_KEY")
    mgr = LLMManager()

    paths = _key_paths()

    if os.getenv("OPENAI_API_KEY"):
        enc_path = mgr.save_encrypted_api_key(
            provider="openai",
            plaintext_key=os.environ["OPENAI_API_KEY"],
            shared_secret=shared,
        )
        print("Saved OpenAI key ->", enc_path)
    else:
        print("Skipping OpenAI: OPENAI_API_KEY not set.")

    if os.getenv("ANTHROPIC_API_KEY"):
        enc_path = mgr.save_encrypted_api_key(
            provider="claude",
            plaintext_key=os.environ["ANTHROPIC_API_KEY"],
            shared_secret=shared,
        )
        print("Saved Claude key ->", enc_path)
    else:
        print("Skipping Claude: ANTHROPIC_API_KEY not set.")

    if os.getenv("GEMINI_API_KEY"):
        enc_path = mgr.save_encrypted_api_key(
            provider="gemini",
            plaintext_key=os.environ["GEMINI_API_KEY"],
            shared_secret=shared,
        )
        print("Saved Gemini key ->", enc_path)
    else:
        print("Skipping Gemini: GEMINI_API_KEY not set.")

    print("Key files:", paths)

def _smoke_one(provider: str) -> None:
    print(f"step 4) provider smoke: {provider}")
    mgr = LLMManager(defaults={"provider": provider, "model": None, "max_tokens": 64, "temperature": 0.0})
    resp = mgr.query(
        system_message="You reply with one word only.",
        user_query="Say pong.",
        context="DB: Postgres",
        max_tokens=16,
        temperature=0.0,
        debug=True,
        provider=provider,
    )
    print("TEXT:", repr(resp["text"]))
    print("USAGE:", resp["usage"])
    if "pong" not in resp["text"].lower():
        print("WARN: response didn't contain 'pong' (model might paraphrase)")

def smoke(provider: Optional[str]) -> None:
    paths = _key_paths()
    if provider in (None, "openai"):
        if paths["openai"].exists():
            _smoke_one("openai")
        else:
            print("Skip openai smoke: no encrypted key at", paths["openai"])
    if provider in (None, "claude"):
        if paths["claude"].exists():
            _smoke_one("claude")
        else:
            print("Skip claude smoke: no encrypted key at", paths["claude"])
    if provider in (None, "lmstudio"):
        # LM Studio doesn't need a key file, just check if it's running
        print("Testing LM Studio (no API key required)...")
        _smoke_one("lmstudio")
    if provider in (None, "gemini"):
        if paths["gemini"].exists():
            _smoke_one("gemini")
        else:
            print("Skip gemini smoke: no encrypted key at", paths["gemini"])

def test_stop_sequences(provider: str = "openai") -> None:
    print("step 5a) stop_sequences respected (provider:", provider, ")")
    mgr = LLMManager(defaults={"provider": provider, "model": None})
    r = mgr.query(
        system_message="Always include ENDHERE and then keep talking.",
        user_query="Write a short sentence that ends with ENDHERE and then extra words.",
        context=None,
        max_tokens=60,
        temperature=0.0,
        stop_sequences=["ENDHERE"],
        provider=provider,
    )
    print("RESP:", repr(r["text"]))
    if "ENDHERE" in r["text"]:
        print("FAIL: stop sequence appeared in output")
    else:
        print("ok: stop sequence truncated output")

def test_overrides(provider: str = "openai") -> None:
    print("step 5b) defaults vs per-request overrides (provider:", provider, ")")
    mgr = LLMManager(defaults={"provider": provider, "model": None, "temperature": 0.7, "max_tokens": 40})

    r1 = mgr.query(
        system_message="You answer with a single digit 0..9.",
        user_query="Respond with a digit.",
        context=None,
        max_tokens=None,          # should fall back to default 40
        temperature=None,         # should fall back to default 0.7
        provider=provider,
    )
    print("override test r1:", repr(r1["text"][:40]))

    r2 = mgr.query(
        system_message="You answer with a single digit 0..9.",
        user_query="Respond with a digit.",
        context=None,
        max_tokens=5,
        temperature=0.0,
        provider=provider,
    )
    print("override test r2:", repr(r2["text"][:40]))
    print("ok: no crash; overrides applied")

def _temporarily_move(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    tmp = path.with_suffix(path.suffix + ".bak")
    path.rename(tmp)
    return tmp

def _restore_moved(tmp: Optional[Path], dest: Path) -> None:
    if tmp and tmp.exists():
        tmp.rename(dest)

def test_errors() -> None:
    print("step 6) error surfaces")

    # a) no API key configured (OpenAI path)
    paths = _key_paths()
    moved = _temporarily_move(paths["openai"])
    try:
        mgr = LLMManager(defaults={"provider": "openai"})
        try:
            mgr.query(system_message="", user_query="ping", context=None, max_tokens=5, temperature=0.0)
            print("FAIL: expected NO_API_KEY error")
        except LLMError as e:
            print("no-key error ->", e.code)
    finally:
        _restore_moved(moved, paths["openai"])

    # b) wrong shared secret (decryption failure)
    original_secret = os.getenv("RDST_LLM_SHARED_KEY", "")
    os.environ["RDST_LLM_SHARED_KEY"] = "WRONG-KEY"
    try:
        mgr = LLMManager()
        try:
            mgr.load_api_key("openai")
            print("FAIL: expected DECRYPT_FAILED")
        except LLMError as e:
            print("wrong-secret error ->", e.code)
    finally:
        if original_secret:
            os.environ["RDST_LLM_SHARED_KEY"] = original_secret
        else:
            os.environ.pop("RDST_LLM_SHARED_KEY", None)

    # c) provider HTTP error (bad model)
    mgr = LLMManager(defaults={"provider": "openai"})
    try:
        mgr.query(system_message="", user_query="hi", context=None, max_tokens=5, temperature=0.0, model="not-a-real-model")
        print("FAIL: expected PROVIDER_HTTP")
    except LLMError as e:
        print("bad-model error ->", e.code, e.status)

def shim_explain(query: str, provider: str = "openai") -> None:
    print("step 7) rdst-style shim call")
    # minimal “rdst analyze” flavor
    mgr = LLMManager(defaults={"provider": provider, "model": None, "max_tokens": 400, "temperature": 0.2})
    txt = mgr.query(
        system_message="You are a SQL tuning assistant. Be concise and actionable.",
        user_query=f"Explain why this query might be slow and propose 3 fixes:\n\n{query}",
        context="DB: Postgres, Schema: ecommerce",
        max_tokens=300,
        temperature=0.2,
        provider=provider,
        debug=False,
    )["text"]
    rdst = RdstResult(True, "Analyze stub – executed LLM.", data={"query": query, "llm": txt})
    print("\n--- llm ---\n", txt, "\n--- rdst ---\n", rdst, "\n-----------------\n")


def main():
    ap = argparse.ArgumentParser(description="Manual POC runner for llm_manager")
    ap.add_argument("--suite", choices=["quick", "all"], help="Run a bundle: 'quick' = sanity + smoke (both) + stop + overrides; 'all' adds error tests")
    ap.add_argument("--save-keys", action="store_true", help="Encrypt & store OPENAI/ANTHROPIC keys using RDST_LLM_SHARED_KEY")
    ap.add_argument("--smoke", choices=["openai", "claude", "gemini", "lmstudio", "both"], help="Run smoke test(s)")
    ap.add_argument("--test-stop", action="store_true", help="Test stop_sequences behavior")
    ap.add_argument("--test-overrides", action="store_true", help="Test default vs per-request overrides")
    ap.add_argument("--test-errors", action="store_true", help="Trigger and verify error surfaces")
    ap.add_argument("--shim-explain", metavar="SQL", help="Run a tiny rdst-style explain with the LLM")
    ap.add_argument("--provider", choices=["openai", "claude", "gemini", "lmstudio"], default="openai", help="Provider for single-provider tests")
    args = ap.parse_args()

    try:
        sanity_check_imports()

        if args.save_keys:
            save_encrypted_keys()

        if args.smoke:
            smoke(None if args.smoke == "both" else args.smoke)

        if args.test_stop:
            test_stop_sequences(provider=args.provider)

        if args.test_overrides:
            test_overrides(provider=args.provider)

        if args.test_errors:
            test_errors()

        if args.shim_explain:
            shim_explain(args.shim_explain, provider=args.provider)

        if args.suite:
            if args.suite == "quick":
                # non-destructive bundle
                smoke(None)                 # both if available
                test_stop_sequences("openai")
                test_overrides("openai")
            elif args.suite == "all":
                smoke(None)
                test_stop_sequences("openai")
                test_overrides("openai")
                test_errors()

        if not any([args.suite, args.save_keys, args.smoke, args.test_stop, args.test_overrides, args.test_errors, args.shim_explain]):
            ap.print_help()

    except LLMError as e:
        print(f"LLMError [{e.code}]: {e}")
        sys.exit(2)
    except SystemExit:
        raise
    except Exception as e:
        print("Fatal:", repr(e))
        sys.exit(1)

if __name__ == "__main__":
    main()