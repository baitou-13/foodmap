(function () {
  const escapeHTML = (s = '') => String(s).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const slug = new URLSearchParams(location.search).get('id');
  const p = (window.FOOD_POSTS || []).find(item => item.slug === slug);
  const root = document.getElementById('post-detail');
  if (!p) { root.innerHTML = `<div class="not-found"><span>404</span><h1>这顿饭暂时找不到</h1><a href="index.html">回到食迹</a></div>`; return; }
  const d = new Date(p.posted_at), date = new Intl.DateTimeFormat('zh-CN', {year:'numeric',month:'long',day:'numeric',weekday:'long'}).format(d);
  document.title = `${p.title || '一顿饭'} · 食迹`;
  const images = (p.images || []).map((img, i) => `<figure class="photo photo-${i % 3}"><img src="assets/food/${encodeURIComponent(p.slug)}/${encodeURIComponent(img)}" alt="帖子配图 ${i+1}" loading="${i ? 'lazy' : 'eager'}"></figure>`).join('');
  root.innerHTML = `
    <article>
      <div class="detail-lead">
        <div class="detail-kicker"><span>${date}</span><span class="location-pending">位置待添加</span></div>
        <h1>${escapeHTML(p.title || '今天吃了什么')}</h1>
        <div class="author"><span class="avatar">${escapeHTML((p.author || '食').slice(0,1))}</span><div><strong>${escapeHTML(p.author || '匿名')}</strong><small>记录于食迹</small></div></div>
      </div>
      ${images ? `<div class="photo-grid">${images}</div>` : ''}
      <div class="story-layout">
        <div class="story"><span class="quote-mark">“</span>${escapeHTML(p.content || '').split(/\n+/).filter(Boolean).map(line => `<p>${line}</p>`).join('')}</div>
        <aside class="detail-aside">
          <div><span>浏览</span><strong>${p.engagement?.views || 0}</strong></div><div><span>喜欢</span><strong>${p.engagement?.likes || 0}</strong></div><div><span>评论</span><strong>${p.engagement?.comments || 0}</strong></div>
          <div class="place-box"><span>地点</span><strong>等待补充坐标</strong><small>未来会在地图中显示准确位置</small></div>
          ${p.url ? `<a class="source-link" href="${escapeHTML(p.url)}" target="_blank" rel="noopener">查看原帖 ↗</a>` : ''}
        </aside>
      </div>
    </article>`;
})();
