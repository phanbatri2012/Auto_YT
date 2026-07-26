import subprocess
import sys
from pathlib import Path

WORKER_SCRIPT = Path(__file__).parent / "chatgpt_worker.py"
PYTHON_EXE = sys.executable

CHAT_URL_MARKER = "###CHAT_URL###"

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
        timeout=2400,
        env=env,
    )

    stderr_output = result.stderr.decode("utf-8", errors="replace")
    if stderr_output:
        import sys
        print(stderr_output, file=sys.stderr)

    if result.returncode != 0:
        raise Exception(f"Playwright worker failed: {stderr_output}")

    output = result.stdout.decode("utf-8", errors="replace")
    
    # Parse out chat_url if worker appended it
    chat_url = ""
    if CHAT_URL_MARKER in output:
        parts = output.rsplit(CHAT_URL_MARKER, 1)
        output = parts[0].rstrip()
        chat_url = parts[1].strip()
    
    return {"script": output, "chat_url": chat_url}
