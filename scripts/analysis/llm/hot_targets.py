"""Hot target detail pass runner.

Runs secondary LLM passes for configured targets (e.g., conflict, politics)
over already chunked transcript text.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests


class HotTargetRunner:
    """Execute targeted detail passes using the provided model/endpoint."""

    def __init__(
        self,
        model: str,
        base_url: str,
        options: Optional[Dict[str, Any]] = None,
        log_mode: str = "quiet",
        output_shapes: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.api_url = f"{base_url.rstrip('/')}/api/generate"
        self.options = options or {}
        self.log_mode = log_mode
        self.output_shapes = output_shapes or {}

    def _get_schema_for_shape(self, shape_name: str) -> Dict[str, Any]:
        """Get the schema for a shape name, checking custom shapes first, then built-ins."""
        shape_name_lower = (shape_name or "").lower()

        # Check custom shapes first
        if shape_name in self.output_shapes:
            return self.output_shapes[shape_name]

        # Built-in presence shape
        if shape_name_lower == "presence":
            return {
                "target": "string",
                "chunk_id": "int",
                "spans": [
                    {
                        "present": "bool",
                        "label": "string (short description if present)",
                        "parties": "[optional array of strings]",
                        "polarity": "positive|negative|neutral|mixed|unclear",
                        "sentiment": "string (mood/stance if applicable)",
                        "evidence": "string (brief quote/paraphrase)",
                        "start_sec": "number or null",
                        "end_sec": "number or null",
                    }
                ],
            }

        # Built-in default span shape
        return {
            "target": "string",
            "chunk_id": "int",
            "spans": [
                {
                    "label": "string (short description)",
                    "parties": "[optional array of strings]",
                    "polarity": "positive|negative|neutral|mixed|unclear",
                    "sentiment": "string (mood/stance if applicable)",
                    "evidence": "string (brief quote/paraphrase)",
                    "start_hint": "string (phrase or mm:ss if possible)",
                    "end_hint": "string (phrase or mm:ss if possible)",
                    "start_sec": "number or null (optional numeric seconds)",
                    "end_sec": "number or null (optional numeric seconds)",
                }
            ],
        }

    def _build_prompt(self, target: Dict[str, Any], chunk_text: str, chunk_id: int) -> str:
        name = target.get("name") or target.get("category") or "target"
        instr = target.get("prompt") or target.get("instruction") or ""
        category = target.get("category") or ""
        output_shape = target.get("output_shape") or "span"

        # Get the schema for this shape
        schema = self._get_schema_for_shape(output_shape)
        schema_json = json.dumps(schema, indent=2)

        # Presence-mode drills (yes/no) use a lighter schema
        if output_shape.lower() == "presence":
            return f"""You are running a targeted presence check "{name}" (category: {category}).
Instruction: {instr}

For the following transcript chunk (chunk_id={chunk_id}), decide if the target is present. Return ONLY valid JSON conforming to this schema:
{schema_json}

Transcript chunk:
{chunk_text[:3000]}

Return only the JSON object."""

        return f"""You are running a targeted pass "{name}" (category: {category}).
Instruction: {instr}

For the following transcript chunk (chunk_id={chunk_id}), extract zero or more spans (can be empty).
Return ONLY valid JSON conforming to this schema:
{schema_json}

Transcript chunk:
{chunk_text[:3000]}

Return only the JSON object."""

    def _call_model(self, prompt: str, chunk_id: int) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": self.options,
        }
        try:
            resp = requests.post(self.api_url, json=payload, timeout=180)
        except Exception as exc:
            if self.log_mode == "verbose":
                print(f"[hot target] HTTP error for chunk {chunk_id}: {exc}")
            return {}
        if resp.status_code != 200:
            if self.log_mode == "verbose":
                print(f"[hot target] API status {resp.status_code} for chunk {chunk_id}")
            return {}
        try:
            body = resp.json()
            raw = body.get("response", "{}")
            if self.log_mode == "verbose":
                print(f"[hot target] chunk {chunk_id} raw:\n{raw}\n", flush=True)
            return json.loads(raw)
        except Exception:
            return {}

    def run_targets(self, targets: List[Dict[str, Any]], chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Run all targets over all chunks; returns mapping target -> results list."""
        results: Dict[str, Any] = {}
        if not targets or not chunks:
            return results
        for target in targets:
            name = target.get("name") or target.get("category") or "target"
            tgt_model = target.get("model") or self.model
            tgt_endpoint = target.get("endpoint") or self.base_url
            runner = self if (tgt_model == self.model and tgt_endpoint == self.base_url) else HotTargetRunner(
                model=tgt_model,
                base_url=tgt_endpoint,
                options=target.get("options") or self.options,
                log_mode=self.log_mode,
                output_shapes=self.output_shapes,
            )
            entries: List[Dict[str, Any]] = []
            for chunk in chunks:
                cid = chunk.get("chunk_id")
                text = chunk.get("text") or ""
                prompt = runner._build_prompt(target, text, cid)
                data = runner._call_model(prompt, cid)
                spans = data.get("spans") if isinstance(data, dict) else []
                entries.append(
                    {
                        "chunk_id": cid,
                        "spans": spans if isinstance(spans, list) else [],
                        "model_used": runner.model,
                        "endpoint": runner.base_url,
                    }
                )
            results[name] = entries
        return results
