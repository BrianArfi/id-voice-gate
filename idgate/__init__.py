"""id-voice-gate: catch Indonesian text that reads like a translation, before it ships.

    import idgate
    r = idgate.check("Tiga langkah, udah.", kind="post")
    g = idgate.gate(draft, kind="caption")
    if not g["passed"]:
        ...  # hold it, do not publish
    else:
        publish(g["final_text"])
"""
__version__ = "1.0.0"

from .core import (  # noqa: E402,F401
    KINDS, REGISTERS, SETTINGS, EXTRA_CHECKS, check, gate, lint, blocking, rewrite_problems,
    detect_register, register_checker, mode, list_guides, guide_path, build_judge_prompt,
)
from .judges import JudgeUnavailable  # noqa: E402,F401
