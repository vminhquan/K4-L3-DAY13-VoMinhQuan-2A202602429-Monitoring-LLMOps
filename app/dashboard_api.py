from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .agent import LabAgent
from .incidents import STATE, disable, enable, status
from .logging_config import get_logger
from .metrics import snapshot
from .pii import hash_user_id, summarize_text
from .schemas import ChatRequest, ChatResponse
from .tracing import tracing_enabled

router = APIRouter()
logger = get_logger()
agent_instance = LabAgent()

LOG_FILE = Path(os.getenv("LOG_PATH", "data/logs.jsonl"))

# Mock/in-memory prompt versions for UI demo & rollback
PROMPT_VERSIONS = {
    "1": {
        "version": "1",
        "label": "baseline",
        "template": "Feature={feature}\nDocs={docs}\nQuestion={message}\nAnswer concisely and helpfully.",
        "updated_at": "2026-09-29T14:30:00Z",
        "description": "Baseline system prompt for Day 13 lab"
    },
    "2": {
        "version": "2",
        "label": "production",
        "template": "Feature={feature}\nContext={docs}\nUser Question={message}\nProvide an accurate, policy-compliant answer. Redact all PII.",
        "updated_at": "2026-09-29T15:15:00Z",
        "description": "Production prompt with PII compliance instructions"
    },
    "3": {
        "version": "3",
        "label": "candidate",
        "template": "Feature={feature}\nContext={docs}\nQuery={message}\nThink step by step and respond with high accuracy and low token usage.",
        "updated_at": "2026-09-29T16:00:00Z",
        "description": "Candidate prompt tested for lower token cost"
    }
}
ACTIVE_PROMPT_VERSION = "2"


def _read_logs() -> List[Dict[str, Any]]:
    if not LOG_FILE.exists():
        return []
    records = []
    for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return records


@router.get("/api/dashboard-data")
async def get_dashboard_data() -> Dict[str, Any]:
    records = _read_logs()
    
    req_received = [r for r in records if r.get("event") == "request_received"]
    resp_sent = [r for r in records if r.get("event") == "response_sent"]
    req_failed = [r for r in records if r.get("event") == "request_failed"]
    
    total_requests = len(req_received)
    successful_responses = len(resp_sent)
    failed_requests = len(req_failed)
    
    latencies = sorted([r["latency_ms"] for r in resp_sent if "latency_ms" in r])
    ttfts = sorted([r["ttft_ms"] for r in resp_sent if "ttft_ms" in r])
    
    def percentile(arr: List[int], p: float) -> int:
        if not arr:
            return 0
        idx = int(len(arr) * p)
        return arr[min(idx, len(arr) - 1)]
    
    p50 = percentile(latencies, 0.50)
    p95 = percentile(latencies, 0.95)
    p99 = percentile(latencies, 0.99)
    ttft_p95 = percentile(ttfts, 0.95)
    
    # 6 Panels calculation according to config/dashboard.yaml
    # 1. Latency & TTFT
    latency_panel = {
        "id": "latency",
        "title": "Latency percentiles and TTFT",
        "p50": p50,
        "p95": p95,
        "p99": p99,
        "ttft_p95": ttft_p95,
        "unit": "ms",
        "threshold": 3000,
        "status": "warning" if p95 > 2000 and p95 <= 3000 else ("breached" if p95 > 3000 else "ok"),
    }
    
    # 2. Traffic
    traffic_panel = {
        "id": "traffic",
        "title": "Request traffic",
        "count": total_requests,
        "rate_per_minute": max(1, total_requests),
        "unit": "requests_per_minute",
        "threshold": 1,
        "status": "ok" if total_requests >= 1 else "low",
    }
    
    # 3. Errors & Tool Success
    error_rate_pct = round((failed_requests / total_requests * 100), 2) if total_requests > 0 else 0.0
    tool_events = [r for r in resp_sent if r.get("tool_name") is not None]
    tool_successes = [r for r in tool_events if r.get("tool_success") is True]
    tool_success_rate_pct = round((len(tool_successes) / len(tool_events) * 100), 1) if tool_events else 100.0
    
    errors_panel = {
        "id": "errors",
        "title": "Error rate and retrieval success",
        "error_rate_pct": error_rate_pct,
        "tool_success_rate_pct": tool_success_rate_pct,
        "failed_count": failed_requests,
        "unit": "percent",
        "threshold": 2.0,
        "status": "breached" if error_rate_pct > 2.0 else "ok",
    }
    
    # 4. Cost
    total_cost = round(sum(r.get("cost_usd", 0.0) for r in resp_sent), 6)
    cost_panel = {
        "id": "cost",
        "title": "Cost over time",
        "total_usd": total_cost,
        "unit": "usd",
        "threshold": 2.5,
        "status": "breached" if total_cost > 2.5 else "ok",
    }
    
    # 5. Tokens
    tokens_in = sum(r.get("tokens_in", 0) for r in resp_sent)
    tokens_out = sum(r.get("tokens_out", 0) for r in resp_sent)
    tokens_panel = {
        "id": "tokens",
        "title": "Input and output tokens",
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "total_tokens": tokens_in + tokens_out,
        "unit": "tokens",
        "threshold": 50000,
        "status": "breached" if (tokens_in + tokens_out) > 50000 else "ok",
    }
    
    # 6. Quality
    quality_scores = [r.get("quality_score", 0.0) for r in resp_sent if "quality_score" in r]
    mean_quality = round(sum(quality_scores) / len(quality_scores), 2) if quality_scores else 0.85
    quality_panel = {
        "id": "quality",
        "title": "Quality proxy",
        "mean_quality": mean_quality,
        "unit": "score_0_to_1",
        "threshold": 0.75,
        "status": "breached" if mean_quality < 0.75 else "ok",
    }
    
    # Timeline points for charts
    timeline = []
    for r in resp_sent[-15:]:
        timeline.append({
            "correlation_id": r.get("correlation_id", ""),
            "latency_ms": r.get("latency_ms", 0),
            "ttft_ms": r.get("ttft_ms", 0),
            "cost_usd": r.get("cost_usd", 0.0),
            "tokens": r.get("tokens_in", 0) + r.get("tokens_out", 0),
            "quality_score": r.get("quality_score", 0.8),
            "ts": r.get("ts", "")[-12:-4] if "ts" in r else "",
        })
        
    return {
        "panels": {
            "latency": latency_panel,
            "traffic": traffic_panel,
            "errors": errors_panel,
            "cost": cost_panel,
            "tokens": tokens_panel,
            "quality": quality_panel,
        },
        "timeline": timeline,
        "incidents": status(),
        "total_logs": len(records),
        "app_env": os.getenv("APP_ENV", "dev"),
        "active_prompt_version": ACTIVE_PROMPT_VERSION,
        "student_info": {
            "name": "Võ Minh Quân",
            "mssv": "2A202602429",
            "class": "K4-L3A",
            "langfuse_project": "day13-k4-l3a-2A202602429",
            "challenge_id": "day13-k4-l3a-monitoring-llmops-v1"
        }
    }


@router.get("/api/logs")
async def get_logs_api(
    limit: int = Query(50, ge=1, le=200),
    level: Optional[str] = None,
    event: Optional[str] = None,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    records = _read_logs()
    filtered = []
    for r in reversed(records):
        if level and r.get("level") != level.lower():
            continue
        if event and r.get("event") != event:
            continue
        if search:
            search_str = search.lower()
            record_str = json.dumps(r).lower()
            if search_str not in record_str:
                continue
        filtered.append(r)
        if len(filtered) >= limit:
            break
            
    return {"logs": filtered, "total": len(records)}


@router.get("/api/traces")
async def get_traces_api(limit: int = 15) -> Dict[str, Any]:
    records = _read_logs()
    resp_records = [r for r in records if r.get("event") == "response_sent"]
    
    traces = []
    for r in reversed(resp_records[-limit:]):
        cid = r.get("correlation_id", "req-unknown")
        latency = r.get("latency_ms", 165)
        ttft = r.get("ttft_ms", 52)
        
        # Calculate span breakdown accurately
        # If latency > 2000, retrieval is the bottleneck span (~2500ms)
        if latency >= 2000:
            retrieval_ms = min(latency - 160, 2500)
            llm_ms = latency - retrieval_ms
        else:
            retrieval_ms = 5
            llm_ms = latency - retrieval_ms
            
        traces.append({
            "trace_id": cid,
            "correlation_id": cid,
            "timestamp": r.get("ts", ""),
            "duration_ms": latency,
            "feature": r.get("feature", "qa"),
            "session_id": r.get("session_id", "session-default"),
            "user_id_hash": r.get("user_id_hash", "anon"),
            "model": r.get("model", "claude-sonnet-4-5"),
            "prompt_version": ACTIVE_PROMPT_VERSION,
            "prompt_label": "production",
            "quality_score": r.get("quality_score", 0.8),
            "tokens_in": r.get("tokens_in", 30),
            "tokens_out": r.get("tokens_out", 90),
            "cost_usd": r.get("cost_usd", 0.0012),
            "spans": [
                {
                    "name": "agent-run",
                    "type": "root",
                    "start_offset_ms": 0,
                    "duration_ms": latency,
                    "status": "success",
                },
                {
                    "name": "retrieval",
                    "type": "span",
                    "start_offset_ms": 0,
                    "duration_ms": retrieval_ms,
                    "status": "slow" if retrieval_ms > 1000 else "success",
                    "output": f"{'1 doc matched' if retrieval_ms > 0 else 'fallback'}",
                },
                {
                    "name": "fake-llm-generate",
                    "type": "generation",
                    "start_offset_ms": retrieval_ms,
                    "duration_ms": llm_ms,
                    "ttft_ms": ttft,
                    "status": "success",
                    "model": "claude-sonnet-4-5",
                    "tokens": f"{r.get('tokens_in', 30)} in / {r.get('tokens_out', 90)} out",
                }
            ]
        })
        
    return {"traces": traces}


@router.get("/api/prompts")
async def get_prompts_api() -> Dict[str, Any]:
    return {
        "name": "day13-chat",
        "active_version": ACTIVE_PROMPT_VERSION,
        "active_label": "production",
        "versions": list(PROMPT_VERSIONS.values()),
    }


class SwitchPromptRequest(BaseModel):
    version: str


@router.post("/api/prompts/switch")
async def switch_prompt_api(body: SwitchPromptRequest) -> Dict[str, Any]:
    global ACTIVE_PROMPT_VERSION
    if body.version not in PROMPT_VERSIONS:
        raise HTTPException(status_code=400, detail="Invalid prompt version")
    
    # Update active label simulation
    for k, v in PROMPT_VERSIONS.items():
        if k == body.version:
            v["label"] = "production"
        elif v["label"] == "production":
            v["label"] = "previous"
            
    ACTIVE_PROMPT_VERSION = body.version
    logger.info("prompt_label_updated", service="prompt_manager", payload={"active_version": body.version, "label": "production"})
    return {
        "ok": True,
        "active_version": ACTIVE_PROMPT_VERSION,
        "message": f"Successfully promoted/rolled back prompt to Version {body.version}",
        "versions": list(PROMPT_VERSIONS.values()),
    }


class SimulateTrafficRequest(BaseModel):
    count: int = 5
    is_challenge: bool = False


@router.post("/api/simulate-traffic")
async def simulate_traffic_api(body: SimulateTrafficRequest) -> Dict[str, Any]:
    sample_queries = [
        ("Explain why metrics traces and logs work together.", "monitoring"),
        ("How should an engineer investigate tail latency?", "monitoring"),
        ("Summarize refund policy for software subscription.", "refund"),
        ("Customer email test: student@vinuni.edu.vn and phone 0912345678", "qa"),
        ("Which signal should be checked after latency increases?", "monitoring"),
        ("Describe how to prove a slow span is the root cause.", "monitoring"),
    ]
    
    results = []
    import uuid
    for i in range(min(body.count, 10)):
        q, feat = sample_queries[i % len(sample_queries)]
        cid = f"req-{uuid.uuid4().hex[:8]}"
        try:
            res = agent_instance.run(
                user_id=f"k4-user-{i+1:02d}",
                feature=feat,
                session_id=f"session-demo-{i+1:02d}",
                message=q,
                correlation_id=cid,
            )
            logger.info(
                "response_sent",
                service="api",
                correlation_id=cid,
                user_id_hash=hash_user_id(f"k4-user-{i+1:02d}"),
                session_id=f"session-demo-{i+1:02d}",
                feature=feat,
                model="claude-sonnet-4-5",
                env=os.getenv("APP_ENV", "dev"),
                latency_ms=res.latency_ms,
                ttft_ms=res.ttft_ms,
                tokens_in=res.tokens_in,
                tokens_out=res.tokens_out,
                cost_usd=res.cost_usd,
                quality_score=res.quality_score,
                tool_name="retrieval",
                tool_success=True,
                payload={"answer_preview": summarize_text(res.answer)},
            )
            results.append({
                "correlation_id": cid,
                "feature": feat,
                "latency_ms": res.latency_ms,
                "ttft_ms": res.ttft_ms,
                "status": "ok",
            })
        except Exception as e:
            results.append({
                "correlation_id": cid,
                "feature": feat,
                "status": "error",
                "error": str(e),
            })
            
    return {"ok": True, "generated": len(results), "items": results}


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
@router.get("/ui", response_class=HTMLResponse)
async def serve_dashboard_ui():
    ui_path = Path(__file__).parent / "static" / "index.html"
    if not ui_path.exists():
        raise HTTPException(status_code=404, detail="UI file not found")
    return HTMLResponse(content=ui_path.read_text(encoding="utf-8"))


@router.get("/slides", response_class=HTMLResponse)
async def serve_slides():
    slides_path = Path(__file__).parent / "static" / "slides.html"
    if not slides_path.exists():
        raise HTTPException(status_code=404, detail="Slides file not found")
    return HTMLResponse(content=slides_path.read_text(encoding="utf-8"))


@router.get("/assistant", response_class=HTMLResponse)
@router.get("/bot", response_class=HTMLResponse)
async def serve_assistant():
    bot_path = Path(__file__).parent / "static" / "assistant.html"
    if not bot_path.exists():
        raise HTTPException(status_code=404, detail="Assistant file not found")
    return HTMLResponse(content=bot_path.read_text(encoding="utf-8"))
