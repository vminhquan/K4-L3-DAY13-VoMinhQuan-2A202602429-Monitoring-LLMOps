# Báo cáo cá nhân — K4-L3A Day 13 Monitoring & LLMOps

> Mỗi học viên hoàn thiện một file duy nhất này. Khi dẫn evidence, dùng đường dẫn tương đối, ví dụ `evidence/07-trace-waterfall.png`.

## 1. Thông tin học viên

- **Họ và tên:** Võ Minh Quân
- **MSSV:** 2A202602429
- **Lớp:** K4-L3A
- **Repository URL:** https://github.com/VinUni-AI20k/K4-L3A-Day13-Monitoring-LLMOps.git
- **Commit SHA cuối:** 13b606680ae4a3072eda90334959b632fe4ecba0
- **Challenge ID:** day13-k4-l3a-monitoring-llmops-v1
- **Tên project Langfuse cá nhân:** `day13-k4-l3a-2A202602429`

## 2. Evidence index

Điền đúng đường dẫn tới evidence thực tế. Có thể đổi tên hoặc dùng nhiều ảnh nếu cần.

| Evidence | Đường dẫn |
|---|---|
| Pytest cuối | `evidence/01-pytest.png` (kèm `evidence/01-pytest.txt`) |
| Log validator | `evidence/02-log-validator.png` (kèm `evidence/02-log-validator.txt`) |
| Dashboard validator | `evidence/03-dashboard-validator.png` (kèm `evidence/03-dashboard-validator.txt`) |
| Structured log | `evidence/04-structured-log.png` |
| PII redaction | `evidence/05-pii-redaction.png` |
| Trace list | `evidence/06-trace-list.png` |
| Trace waterfall | `evidence/07-trace-waterfall.png` |
| Trace metadata | `evidence/08-trace-metadata.png` |
| Prompt versions | `evidence/09-prompt-versions.png` |
| Prompt rollback | `evidence/10-prompt-rollback.png` |
| Dashboard runtime | `evidence/11-dashboard-overview.png` |
| Incident metric | `evidence/12-incident-metric.png` |
| Incident log | `evidence/13-incident-log.png` |
| Incident trace | `evidence/14-incident-trace.png` |

## 3. Kết quả kỹ thuật

| Nội dung | Baseline | Kết quả cuối | Nhận xét |
|---|---|---|---|
| `validate_logs.py` | 55/100 | 100/100 | Đạt toàn bộ 4/4 tiêu chí: JSON schema, Correlation ID propagation, Log enrichment và PII scrubbing |
| `validate_dashboard.py` | 0/6 panel hợp lệ | 6/6 panel | Hợp lệ toàn bộ 6/6 panel trong dashboard contract (latency, traffic, errors, cost, tokens, quality) |
| `pytest` | 18 passed, 4 failed | 22 passed, 0 failed | Toàn bộ 22/22 unit và integration tests đều pass thành công (0.83s) |
| Số traces hợp lệ | 0 | ≥ 10 traces | Đã thu thập trên 10 traces hợp lệ trong project Langfuse cá nhân, có phân rã span cha-con |
| Số PII leak | 4 rò rỉ (Email, Phone, CCCD, Card) | 0 rò rỉ | Scrubbed sạch sẽ mọi trường nhạy cảm trong log trước khi ghi đĩa |
| Latency P95 / TTFT P95 | Baseline ~165ms / 55ms | Incident: 4673ms / 55ms | TTFT giữ ổn định ~52-55ms, độ trễ P95 tăng vọt do RAG retrieval bị chậm |
| Retrieval success rate | 100% | 100% | Dịch vụ RAG hoàn thành với `tool_success: true`, không gây 500 error |

## 4. Logging và PII

- **Cách tạo/nhận và truyền correlation ID:**
  - Nhận và kiểm tra header `x-request-id` từ request đến với regex chuẩn `^req-[a-f0-9]{8}$`. Nếu thiếu hoặc không hợp lệ, middleware tự động sinh mới theo format `req-{uuid.uuid4().hex[:8]}` trong `app/middleware.py`.
  - Sử dụng `structlog.contextvars.clear_contextvars()` ở đầu mỗi request để cô lập context, tránh tình trạng rò rỉ ID giữa các luồng/request bất đồng bộ.
  - Bind `correlation_id` vào contextvars của structlog, gán vào `request.state.correlation_id`, inject vào response header `X-Request-ID`, và truyền vào `LabAgent.run(..., correlation_id=correlation_id)` để đồng bộ sang Langfuse trace metadata.
- **Các metadata được ghi vào structured log:**
  - Thông tin chung chuẩn hóa: `ts` (ISO-8601 UTC), `service` ("api"), `event` ("request_received", "response_sent", "request_failed"), `level` ("info", "error"), `correlation_id`.
  - Context enrichment: `user_id_hash` (băm SHA256 lấy 12 ký tự đầu để bảo vệ danh tính), `session_id`, `feature`, `model`, `env`.
  - Metric fields tại event `response_sent`: `latency_ms`, `ttft_ms`, `tokens_in`, `tokens_out`, `cost_usd`, `quality_score`, `tool_name`, `tool_success`, và `payload` (chứa `answer_preview` tóm tắt tối đa 80 ký tự đã được lọc PII).
- **Cách bảo đảm PII được scrub trước khi ghi:**
  - Thiết lập processor chuyên biệt `mask_pii_processor` trong pipeline của `structlog` (`app/logging_config.py`), chạy trước bước serialize JSON (`JSONRenderer`) và ghi ra file/stdout.
  - Processor đệ quy kiểm tra toàn bộ dictionary, list và string values trong log event dict.
  - Sử dụng regex nhận diện và thay thế:
    - Email: `r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"` → `[REDACTED_EMAIL]`
    - Số điện thoại VN (+84 hoặc đầu 0x): `r"(?:\+84|0)(?:3[2-9]|5[6|8|9]|7[0|6-9]|8[1-9]|9[0-9])[0-9]{7}\b"` → `[REDACTED_PHONE]`
    - CCCD/CMND (9 hoặc 12 chữ số): `r"\b(?:\d{12}|\d{9})\b"` → `[REDACTED_CCCD]`
    - Thẻ ngân hàng/tín dụng (13–16 chữ số): `r"\b(?:\d[ -]*?){13,16}\b"` → `[REDACTED_CARD]`
- **Cách kiểm chứng kết quả:**
  - Chạy `python scripts/validate_logs.py` đạt 100/100 điểm tuyệt đối, 0 potential PII leaks.
  - Chạy test suite `pytest tests/test_pii.py` và `tests/test_validate_logs.py` đều PASSED. Kiểm tra file `data/logs.jsonl` thực tế thấy mọi thông tin nhạy cảm đều được che hoàn toàn bằng placeholder tương ứng.

## 5. Tracing và prompt versioning

- **Cách xác nhận traces do chính tôi tạo trong project cá nhân:**
  - File cấu hình `.env` được cấu hình `LANGFUSE_PUBLIC_KEY` và `LANGFUSE_SECRET_KEY` kết nối trực tiếp đến project Langfuse cá nhân `day13-k4-l3a-2A202602429`.
  - Trên màn hình dashboard của Langfuse Cloud hiển thị đúng tên project `day13-k4-l3a-2A202602429`. Danh sách traces khớp chính xác về mốc thời gian, `session_id`, `user_id_hash` và `correlation_id` được tạo ra từ máy local.
- **Cấu trúc root/retrieval/generation observations:**
  - Root observation: `@observe(name="agent-run")` bọc toàn bộ hàm `LabAgent.run()`.
  - Child observation 1 (retrieval): `@observe(name="retrieval", as_type="span")` trong `app/mock_rag.py` bọc hàm `retrieve()`, theo dõi thời gian truy vấn vector context.
  - Child observation 2 (generation): `@observe(name="fake-llm-generate", as_type="generation")` trong `app/mock_llm.py` bọc hàm `generate()`, nhận managed prompt từ context và ghi nhận thông tin mô hình, token usage và output generation.
- **Cách nối trace với log:**
  - Sử dụng chung `correlation_id`. Trong log, ID này nằm ở trường `correlation_id`. Trong Langfuse, ID này được đính kèm vào trace metadata thông qua `propagate_attributes(metadata={"correlation_id": correlation_id, ...})`.
  - Khi cần phân tích một log bất thường, chỉ cần sao chép `correlation_id` và lọc trên giao diện Langfuse Traces bằng bộ lọc `metadata.correlation_id == <id>`.
- **Prompt name:** `day13-chat`
- **Version/label baseline:** Version 1 (label `production` ban đầu)
- **Version/label candidate:** Version 2 (label `candidate` sau khi cập nhật nội dung prompt)
- **Trace ID của mỗi version:**
  - Trace ID version 1: Trace ghi nhận với metadata `prompt_version = "1"` và `prompt_label = "production"`
  - Trace ID version 2: Trace ghi nhận với metadata `prompt_version = "2"` và `prompt_label = "candidate"`
- **Cách promote và rollback `production`:**
  - **Promote**: Vào Langfuse UI > Prompts > `day13-chat`, chọn Version 2 và gán label `production`.
  - **Rollback**: Khi phát hiện lỗi hoặc degradation về latency/cost/quality, gán lại label `production` về Version 1. Ứng dụng qua `resolve_prompt` (`app/prompt_manager.py`) luôn truy vấn prompt theo label `production`, do đó sẽ ngay lập tức chuyển về phiên bản an toàn mà không cần sửa code hay khởi động lại service.

## 6. Dashboard, SLO và alerts

- **Dashboard và sáu panel:**
  - Panel 1 (`latency`): Theo dõi Latency percentiles (P50, P95, P99) và TTFT P95. Ngưỡng P95 <= 3000ms.
  - Panel 2 (`traffic`): Request traffic (count và rate_per_minute). Ngưỡng rate >= 1 req/min.
  - Panel 3 (`errors`): Error rate (%) và tỷ lệ thành công của retrieval tool (tool_success_rate_pct). Ngưỡng error_rate <= 2%.
  - Panel 4 (`cost`): Chi phí tích lũy theo phút và tổng chi phí (USD). Ngưỡng tổng chi phí <= $2.5.
  - Panel 5 (`tokens`): Tổng token vào (tokens_in) và token ra (tokens_out). Ngưỡng tổng <= 50,000 tokens.
  - Panel 6 (`quality`): Điểm chất lượng trung bình (mean quality proxy score). Ngưỡng trung bình >= 0.75.
- **SLO và lý do chọn:**
  - Primary SLO: `fast_successful_requests` với mục tiêu 99.5% requests thành công và có `latency_ms <= 3000ms` trong chu kỳ rolling window 28 ngày.
  - Lý do: Đây là ứng dụng hỏi đáp AI thời gian thực; trải nghiệm người dùng suy giảm nghiêm trọng nếu độ trễ vượt quá 3 giây. Ngưỡng 3000ms đủ bao quát độ biến thiên của mạng và thời gian sinh token của LLM, đồng thời phát hiện sớm các hiện tượng nghẽn RAG.
- **Cách tính error budget:**
  - Error budget = `100% - 99.5% = 0.5%`.
  - Ví dụ: Với lưu lượng 100,000 requests trong 28 ngày, hệ thống chỉ cho phép tối đa 500 requests không đạt chuẩn (bị lỗi 5xx hoặc latency > 3000ms). Khi error budget tiêu hao nhanh (burn rate cao), đội ngũ kỹ thuật phải dừng phát hành tính năng mới để tập trung tối ưu độ tin cậy.
- **Ba alert và runbook tương ứng:**
  - Alert 1 (`high_latency_p95`): Độ trễ P95 > 3000ms kéo dài trong 5 phút (severity: critical). Runbook: [docs/alerts.md#alert-1](../docs/alerts.md) — Phân tích trace waterfall để xác định span chậm (RAG hay LLM), kiểm tra tài nguyên hạ tầng.
  - Alert 2 (`high_error_rate`): Tỷ lệ lỗi > 2% kéo dài trong 3 phút (severity: critical). Runbook: [docs/alerts.md#alert-2](../docs/alerts.md) — Lọc log theo event `request_failed` và `error_type`, kiểm tra kết nối downstream LLM và Vector Store.
  - Alert 3 (`cost_spike`): Chi phí vượt quá $2.5/giờ kéo dài 10 phút (severity: warning). Runbook: [docs/alerts.md#alert-3](../docs/alerts.md) — Kiểm tra token usage theo user/feature, ngăn chặn tấn công spam hoặc prompt bị lặp vô tận.

## 7. Điều tra challenge

- **Challenge ID:** `day13-k4-l3a-monitoring-llmops-v1`
- **Khoảng thời gian điều tra:** `2026-09-29T09:13:15Z` đến `2026-09-29T09:13:31Z` (sau khi kích hoạt `python scripts/inject_incident.py`)
- **Triệu chứng từ metrics:**
  - Panel `latency` ghi nhận P95 latency tăng vọt từ mức baseline ~165ms lên trên **2660ms – 4673ms**, vượt ngưỡng SLO và kích hoạt alert độ trễ cao.
  - Panel TTFT vẫn giữ ở mức 51ms – 55ms.
  - Panel `errors` ghi nhận error rate = 0% (tất cả 5 requests đều trả về HTTP 200). Tín hiệu này chứng tỏ hệ thống không bị crash, mà đang bị nghẽn (degradation) ở khâu xử lý nghiệp vụ trước generation.
- **Log line và correlation ID liên quan:**
  - Correlation ID bất thường: `req-d33ea19b` (latency: 4673ms) và `req-e864ce7a` (latency: 2665ms).
  - Trích xuất log line từ `data/logs.jsonl`:
    ```json
    {"service": "api", "latency_ms": 4673, "ttft_ms": 55, "tokens_in": 44, "tokens_out": 128, "cost_usd": 0.002052, "quality_score": 0.8, "tool_name": "retrieval", "tool_success": true, "payload": {"answer_preview": "Starter answer. You should improve this output logic and add better quality chec..."}, "event": "response_sent", "user_id_hash": "ed72e61117f6", "correlation_id": "req-d33ea19b", "env": "dev", "model": "claude-sonnet-4-5", "session_id": "k4-l3a-challenge-s05", "feature": "monitoring", "level": "info", "ts": "2026-09-29T09:13:19.846711Z"}
    ```
- **Trace ID và span gây ảnh hưởng:**
  - Tra cứu trace trên Langfuse bằng `metadata.correlation_id = req-d33ea19b`.
  - Phân tích biểu đồ Waterfall của trace:
    - Root observation `agent-run`: tổng thời gian 4673ms.
    - Span con `retrieval`: kéo dài **2500ms** (chiếm phần lớn thời gian xử lý của request).
    - Child generation `fake-llm-generate`: chỉ mất ~150ms.
  - Span gây ảnh hưởng chính: **`retrieval`** trong `app/mock_rag.py`.
- **Root cause:**
  - Lỗi suy thoái hiệu năng tại khâu RAG Retrieval (`rag_slow`), do sự cố mô phỏng đặt `STATE["rag_slow"] = True` làm cho hàm `retrieve()` bị block 2.5 giây khi xử lý các query thuộc feature `monitoring`.
- **Fix action:**
  - Hủy trạng thái cờ sự cố (`STATE["rag_slow"] = False`).
  - Thiết lập timeout cho hàm `retrieve` (ví dụ hard timeout 1.0s); nếu vượt quá thời gian, fallback về context mặc định thay vì để treo luồng của người dùng.
- **Preventive measure:**
  - Bổ sung panel giám sát riêng cho độ trễ RAG: `retrieval_latency_p95`.
  - Cài đặt Circuit Breaker cho dịch vụ Vector Store / Embedding service.
  - Thêm tầng caching (Redis) cho các câu hỏi phổ biến để giảm tải truy vấn lặp lại vào vector database.

## 8. Giải thích và tự đánh giá

- **Một quyết định kỹ thuật quan trọng và lý do:**
  - Đưa cơ chế PII Scrubbing vào ngay trong custom log processor của `structlog` (trước khâu JSON serialization) thay vì làm sạch rải rác ở từng router/service. Lý do: Giúp loại bỏ hoàn toàn khả năng lập trình viên quên redact PII khi tạo log mới, đảm bảo tính tuân thủ bảo mật tập trung và không rò rỉ dữ liệu nhạy cảm ra ổ đĩa hay hệ thống giám sát tập trung.
- **Một lỗi/blocker đã gặp:**
  - Lỗi test `test_agent_records_prompt_version_with_v4_observation_api` bị fail do lệnh `langfuse_client.update_current_span` ghi đè generation metadata sau khi kết thúc `llm.generate()`, khiến phần tử cuối cùng `client.span_updates[-1]` không chứa thông tin phiên bản prompt mà test mong đợi.
- **Cách tìm nguyên nhân và xử lý:**
  - So sánh chi tiết diff giữa expected dict và actual dict từ thông báo lỗi của `pytest`. Nhận thấy `span_updates[-1]` đang chứa các trường token/cost của generation thay vì prompt metadata. Sau đó đã cấu trúc lại code theo chuẩn Langfuse SDK v4: sử dụng decorator `@observe` trực tiếp cho `retrieve()` và `generate()`, đồng thời loại bỏ các lệnh `update_current_span` thủ công dư thừa ở `LabAgent.run()`.
- **Cách hiểu luồng Metrics → Logs → Traces:**
  - **Metrics**: Cho biết *CÓ VẤN ĐỀ GÌ* và *KHI NÀO* (P95 latency tăng cao vượt ngưỡng SLO trên dashboard).
  - **Logs**: Cho biết *REQUEST NÀO* bị ảnh hưởng (dùng bộ lọc thời gian và log level để tìm ra các dòng log có latency cao và lấy `correlation_id`).
  - **Traces**: Cho biết *TẠI ĐÂU VÀ NGUYÊN NHÂN TẠI SAO* (dùng `correlation_id` mở biểu đồ waterfall trace để bóc tách từng span con, phát hiện chính xác span `retrieval` bị nghẽn 2.5s).
- **Vai trò của prompt version, token/cost, SLO hoặc rollback trong vận hành LLM:**
  - Prompt Versioning cho phép quản lý các thay đổi prompt có hệ thống, tách biệt logic nghiệp vụ khỏi mã nguồn ứng dụng và cho phép rollback tức thì khi prompt mới gây ảo giác hoặc làm tăng đột biến chi phí token.
  - Giám sát token và chi phí là yêu cầu sống còn của hệ thống LLMOps để tránh rủi ro tài chính do prompt bị tấn công injection hoặc lặp vô tận.
  - SLO và Error Budget cung cấp thước đo định lượng khách quan để cân bằng giữa tốc độ cải tiến tính năng và độ ổn định của hệ thống.
- **Điều quan trọng nhất đã học:**
  - Khái niệm Observability trong hệ thống LLM không chỉ dừng lại ở logging truyền thống, mà đòi hỏi sự kết nối mắt xích chặt chẽ giữa Metrics, Logs và Traces thông qua Correlation ID để khoanh vùng và xử lý sự cố nhanh chóng.
- **Hạn chế hoặc phần chưa hoàn thành, nếu có:**
  - Hệ thống hiện tại đang sử dụng các module mock cho LLM và RAG; trong môi trường sản xuất thực tế cần bổ sung cơ chế retry có jitter, streaming token latency tracking và tích hợp webhook cảnh báo thời gian thực về Slack/Discord.

## 9. Checklist trước khi nộp

- [x] Kết quả và evidence thuộc commit SHA cuối.
- [x] Tất cả ảnh/output mở được bằng đường dẫn tương đối.
- [x] Incident evidence nối đúng metric → log → trace.
- [x] Trace/prompt evidence thuộc project Langfuse cá nhân và ảnh không lộ key/secret.
- [x] Repository chạy lại được theo README.
- [x] Không có secret, API key, PII thô hoặc evidence của người khác/lớp khác.
- [x] URL repo và commit SHA cuối đã được nộp trên LMS/Codelabs.
