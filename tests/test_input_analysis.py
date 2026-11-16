import importlib.util
import json
from pathlib import Path


def test_input_analysis_separates_characters_and_missing_usage(tmp_path):
    spec = importlib.util.spec_from_file_location("input_analysis", Path(__file__).resolve().parents[1] / "scripts/analyze_prompt_inputs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = [{"screen": "prepare", "decision": {"turn": 1,
             "prompt": "## 本局经历\n- 节点1\n## 界面上出现的条目\n- [角色] 甲:技能\n"}},
            {"screen": "prepare", "decision": {"turn": 1, "run_notes": "新计划",
             "prompt": "## 本局经历\n- 节点1\n- 节点2\n## 界面上出现的条目\n- [角色] 甲:技能\n"}},
            {"screen": "prepare", "decision": {"turn": 2, "prompt": "## 执行结果\n完成"}}]
    (tmp_path / "steps.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n{broken", encoding="utf-8")
    result = module.analyze(tmp_path)
    assert result["decisions"] == 3 and result["prep_openings"] == 2 and result["malformed_lines"] == 1
    assert result["entity_line_chars"]["exact_repeat_share"] == 0.5
    assert result["reported_input_tokens"] is None and not result["usage_recorded"]
    assert result["run_notes_updates"] == 1
    assert result["history_first_last_chars"][1] > result["history_first_last_chars"][0]
