from __future__ import annotations

import html
from urllib.parse import quote

from adwatch.dashboard.views import (
    _page_styles,
    render_navigation,
)
from adwatch.knowledge.search import KnowledgeReadModel
from adwatch.storage.db import Database


def render_knowledge_page(database: Database, *, query: str = "") -> str:
    """渲染知识库首页。"""
    read_model = KnowledgeReadModel(database)
    results = read_model.search(query) if query.strip() else []
    status = read_model.status()

    rows = "".join(
        f"""
        <article style="margin-bottom:16px;padding:16px;background:white;
                         border-radius:8px;border:1px solid var(--color-border);">
          <h3 style="margin:0 0 8px;">
            <a href="/knowledge/document?id={quote(str(item['document_id']))}"
               style="color:var(--color-primary);text-decoration:none;">
              {html.escape(str(item.get('title_zh') or item['title_original']))}
            </a>
          </h3>
          <p style="margin:0 0 8px;color:var(--color-text-secondary);font-size:0.88rem;">
            {html.escape(str(item.get('summary_zh') or '待生成摘要'))}
          </p>
          <p style="margin:0 0 8px;color:var(--color-text-muted);font-size:0.82rem;">
            {html.escape(str(item.get('snippet') or ''))}
          </p>
          <a href="{html.escape(str(item['canonical_url']), quote=True)}"
             target="_blank" rel="noopener noreferrer"
             style="color:var(--color-primary);font-size:0.82rem;">查看原文 →</a>
        </article>
        """
        for item in results
    ) or "<p style='color:var(--color-text-muted);'>输入关键词搜索规则与虾皮大学内容。</p>"

    search_box = f"""
    <form method="get" style="display:flex;gap:8px;margin:16px 0;">
      <input name="q" value="{html.escape(query, quote=True)}"
             placeholder="搜索规则与虾皮大学..."
             style="flex:1;padding:10px 14px;border:1px solid var(--color-border);
                    border-radius:8px;font-size:0.9rem;">
      <button type="submit"
              style="padding:10px 20px;background:var(--color-primary);color:white;
                     border:none;border-radius:8px;cursor:pointer;font-size:0.9rem;">
        搜索
      </button>
    </form>
    """

    body = f"""
    <div class="module-hero">
      <h1>Shopee 泰国知识库</h1>
      <p style="color:rgba(255,255,255,0.85);font-size:0.88rem;">
        文章 {status['documents']} · 待生成摘要 {status['pending_enrichments']} ·
        中文摘要由 AI 生成，请以 Shopee 原文为准
      </p>
    </div>
    {search_box}
    <div class="panel">
      <div class="panel-header">
        <h2>搜索结果</h2>
        <span class="panel-desc">共 {len(results)} 条</span>
      </div>
      <div style="padding:16px;">
        {rows}
      </div>
    </div>
    """

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>知识库 - AdWatch</title>
  <style>{_page_styles()}</style>
</head>
<body>
  {render_navigation("/knowledge")}
  <main style="max-width:1000px;margin:0 auto;padding:24px;">
    {body}
  </main>
</body>
</html>"""


def render_knowledge_detail(database: Database, document_id: str) -> str:
    """渲染知识库文档详情页。"""
    read_model = KnowledgeReadModel(database)
    item = read_model.document(document_id)

    versions = "".join(
        f"<li>版本 {version['version_number']} · "
        f"{html.escape(str(version['retrieved_at']))}</li>"
        for version in item["versions"]
    )

    body = f"""
    <div class="module-hero">
      <h1>{html.escape(str(item.get('title_zh') or item['title_original']))}</h1>
      <p style="color:rgba(255,255,255,0.85);font-size:0.88rem;">
        AI 生成摘要，请以 Shopee 原文为准
      </p>
    </div>
    <div class="panel">
      <div class="panel-header">
        <h2>摘要</h2>
      </div>
      <div style="padding:16px;">
        <p>{html.escape(str(item.get('summary_zh') or '待生成摘要'))}</p>
      </div>
    </div>
    <div class="panel">
      <div class="panel-header">
        <h2>原文内容</h2>
      </div>
      <div style="padding:16px;white-space:pre-wrap;font-size:0.85rem;
                  line-height:1.6;color:var(--color-text-secondary);">
        {html.escape(str(item['content_original']))}
      </div>
    </div>
    <div style="text-align:right;margin:16px 0;">
      <a href="{html.escape(str(item['canonical_url']), quote=True)}"
         target="_blank" rel="noopener noreferrer"
         style="color:var(--color-primary);">查看原文 →</a>
    </div>
    <div class="panel">
      <div class="panel-header">
        <h2>历史版本</h2>
      </div>
      <div style="padding:16px;">
        <ul style="margin:0;padding-left:20px;color:var(--color-text-secondary);">
          {versions}
        </ul>
      </div>
    </div>
    """

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>知识库详情 - AdWatch</title>
  <style>{_page_styles()}</style>
</head>
<body>
  {render_navigation("/knowledge")}
  <main style="max-width:1000px;margin:0 auto;padding:24px;">
    <a href="/knowledge" style="color:var(--color-primary);text-decoration:none;
                                font-size:0.88rem;display:inline-block;margin-bottom:16px;">
      ← 返回知识库
    </a>
    {body}
  </main>
</body>
</html>"""