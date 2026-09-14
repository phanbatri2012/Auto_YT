import json
import os
import subprocess
import sys
from pathlib import Path

from auto_yt.services.chatgpt_runtime import (
    ChatGPTAttentionRequiredError,
    decode_attention_error,
)

SOURCE_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = SOURCE_ROOT.parent
WORKER_SCRIPT = Path(__file__).parent / "chatgpt_worker.py"
PYTHON_EXE = sys.executable

CHAT_URL_MARKER = "###CHAT_URL###"
WORKER_META_MARKER = "###WORKER_META###"


def process_prompt_via_chatgpt(
    prompt_text: str,
    prompt_version: str = "",
    video_id: int | None = None,
    pipeline: dict[str, bool] | None = None,
) -> dict:
    """
    Spawns chatgpt_worker.py as a subprocess.
    Returns dict: {"script": str, "chat_url": str}
    """
    env = os.environ.copy()
    python_paths = [path for path in env.get("PYTHONPATH", "").split(os.pathsep) if path]
    if str(SOURCE_ROOT) not in python_paths:
        python_paths.insert(0, str(SOURCE_ROOT))
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    if prompt_version:
        env["PROMPT_VERSION"] = prompt_version
    if video_id is not None:
        env["VIDEO_ID"] = str(video_id)
    if pipeline is not None:
        env["PROMPT_PIPELINE_JSON"] = json.dumps(pipeline)

    result = subprocess.run(
        [PYTHON_EXE, str(WORKER_SCRIPT)],
        input=prompt_text.encode("utf-8"),
        capture_output=True,
        env=env,
        cwd=str(PROJECT_ROOT),
    )

    stderr_output = result.stderr.decode("utf-8", errors="replace")
    if stderr_output:
        import sys
        print(stderr_output, file=sys.stderr)

    if result.returncode != 0:
        attention_message = decode_attention_error(stderr_output)
        if attention_message is not None:
            raise ChatGPTAttentionRequiredError(
                attention_message
                or "Phiên ChatGPT cần được xác minh thủ công."
            )
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
