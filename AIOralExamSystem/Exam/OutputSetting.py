import json
from html import escape


def build_final_review_output(final_review: dict, output_type: str = "json") -> str:
    """根据输出类型构造最终评价内容，默认返回 JSON 字符串。"""
    if output_type == "html":
        return build_eval_card_html(final_review)
    return json.dumps(final_review, ensure_ascii=False)


def build_eval_card_html(data: dict) -> str:
    """将最终评价 JSON 转成可直接 innerHTML 渲染的纯 HTML。"""
    def h(value) -> str:
        return escape(str(value if value is not None else ""), quote=True)

    scores = data.get("scores") or {}
    dim_scores = scores.get("dimensions") or {}
    max_score = sum(float(score or 0) for score in dim_scores.values())

    dim_html = []
    for index, dim in enumerate(data.get("dimension_summaries") or []):
        dimension = dim.get("dimension", "")
        score = dim_scores.get(dimension, "-")
        open_attr = " open" if index == 0 else ""
        dim_html.append(f"""
    <details class="ec-dim"{open_attr}>
      <summary class="ec-dim-head">
        <span class="ec-dim-arrow">&#9654;</span>
        <span class="ec-dim-name">{h(dimension)}</span>
        <span class="ec-dim-score">{h(score)}</span>
      </summary>
      <div class="ec-dim-body">{h(dim.get("summary", ""))}</div>
    </details>""")

    sections = [
        ("strengths", "优势亮点", "t-s"),
        ("weaknesses", "不足之处", "t-w"),
        ("suggestions", "改进建议", "t-g"),
    ]
    section_html = []
    for key, title, class_name in sections:
        items = data.get(key) or []
        if not items:
            continue
        item_html = "".join(f"<li>{h(item)}</li>" for item in items)
        section_html.append(f"""
    <div class="ec-section">
      <div class="ec-section-title {class_name}">{h(title)}</div>
      <ul>{item_html}</ul>
    </div>""")

    return f"""
<style>
  :root {{
    --ec-bg: #fafafa;
    --ec-border: #e5e7eb;
    --ec-radius: 8px;
    --ec-fg: #1f2937;
    --ec-muted: #6b7280;
    --ec-accent: #059669;
    --ec-warn: #dc2626;
    --ec-info: #2563eb;
  }}

  .ec-card {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: var(--ec-bg);
    border: 1px solid var(--ec-border);
    border-radius: var(--ec-radius);
    padding: 16px;
    font-size: 13px;
    line-height: 1.6;
    color: var(--ec-fg);
    max-width: 520px;
  }}

  .ec-overview {{
    font-size: 12.5px;
    color: var(--ec-muted);
    margin-bottom: 12px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--ec-border);
  }}

  .ec-dim {{
    margin-bottom: 8px;
  }}
  .ec-dim:last-child {{ margin-bottom: 0; }}
  .ec-dim > summary {{
    list-style: none;
  }}
  .ec-dim > summary::-webkit-details-marker {{
    display: none;
  }}

  .ec-dim-head {{
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    user-select: none;
    padding: 6px 0;
  }}
  .ec-dim-head:hover .ec-dim-name {{ color: var(--ec-accent); }}

  .ec-dim-arrow {{
    font-size: 10px;
    color: var(--ec-muted);
    transition: transform .2s;
    width: 14px;
    text-align: center;
    flex-shrink: 0;
  }}
  .ec-dim[open] .ec-dim-arrow {{ transform: rotate(90deg); }}

  .ec-dim-name {{
    flex: 1;
    font-weight: 600;
    font-size: 13px;
    transition: color .15s;
  }}

  .ec-dim-score {{
    font-variant-numeric: tabular-nums;
    font-weight: 600;
    color: var(--ec-accent);
    font-size: 12px;
  }}

  .ec-dim-body {{
    font-size: 12px;
    color: var(--ec-muted);
    padding: 4px 0 6px 22px;
    line-height: 1.65;
  }}

  .ec-section {{
    margin-top: 12px;
    padding-top: 10px;
    border-top: 1px solid var(--ec-border);
  }}
  .ec-section-title {{
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .04em;
    margin-bottom: 6px;
  }}
  .ec-section-title.t-s {{ color: var(--ec-accent); }}
  .ec-section-title.t-w {{ color: var(--ec-warn); }}
  .ec-section-title.t-g {{ color: var(--ec-info); }}

  .ec-section ul {{
    margin: 0;
    padding-left: 16px;
  }}
  .ec-section li {{
    font-size: 12px;
    color: var(--ec-muted);
    margin-bottom: 4px;
    line-height: 1.6;
  }}
  .ec-section li:last-child {{ margin-bottom: 0; }}

  .ec-footer {{
    margin-top: 12px;
    padding-top: 10px;
    border-top: 1px solid var(--ec-border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 12px;
  }}
  .ec-footer-label {{ color: var(--ec-muted); }}
  .ec-footer-total {{
    font-weight: 700;
    font-size: 16px;
    color: var(--ec-accent);
  }}
</style>

<div class="ec-card">
  <div class="ec-overview">{h(data.get("overall_summary", ""))}</div>
  <div class="ec-dims">{''.join(dim_html)}
  </div>
  {''.join(section_html)}
  <div class="ec-footer">
    <span class="ec-footer-label">总分</span>
    <span class="ec-footer-total">{h(scores.get("total", "-"))} / {h(max_score)}</span>
  </div>
</div>
"""
