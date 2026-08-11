import json
import subprocess
import sys
from pathlib import Path

WORKER_SCRIPT = Path(__file__).parent / "chatgpt_worker.py"
PYTHON_EXE = sys.executable

CHAT_URL_MARKER = "###CHAT_URL###"
WORKER_META_MARKER = "###WORKER_META###"


def process_prompt_via_chatgpt(prompt_text: str, prompt_version: str = "") -> dict:
    """
    Spawns chatgpt_worker.py as a subprocess.
    Returns dict: {"script": str, "chat_url": str}
    """
    import os
    env = os.environ.copy()
    if prompt_version:
        env["PROMPT_VERSION"] = prompt_version

    result = subprocess.run(
        [PYTHON_EXE, str(WORKER_SCRIPT)],
        input=prompt_text.encode("utf-8"),
        capture_output=True,
        env=env,
    )

    stderr_output = result.stderr.decode("utf-8", errors="replace")
    if stderr_output:
        import sys
        print(stderr_output, file=sys.stderr)

    if result.returncode != 0:
        raise Exception(f"Playwright worker failed: {stderr_output}")

    output = result.stdout.decode("utf-8", errors="replace")
    
    worker_meta = {
        "warning": "",
        "failed_step": "",
        "complete_for_audio": True,
    }
    if WORKER_META_MARKER in output:
        output, raw_meta = output.rsplit(WORKER_META_MARKER, 1)
        output = output.rstrip()
        try:
            parsed_meta = json.loads(raw_meta.strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError("Playwright worker returned invalid metadata.") from exc
        if isinstance(parsed_meta, dict):
            worker_meta.update(parsed_meta)

    # Parse out chat_url if worker appended it
    chat_url = ""
    if CHAT_URL_MARKER in output:
        parts = output.rsplit(CHAT_URL_MARKER, 1)
        output = parts[0].rstrip()
        chat_url = parts[1].strip()
    
    return {
        "script": output,
        "chat_url": chat_url,
        **worker_meta,
    }
