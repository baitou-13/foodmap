(function () {
  const posts = (window.FOOD_POSTS || []).slice().sort((a, b) => new Date(b.posted_at) - new Date(a.posted_at));
  const timeline = document.getElementById('timeline');
  const yearNav = document.getElementById('year-nav');
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  document.getElementById('post-count').textContent = `${posts.length} 顿 · 持续记录`;

  const escapeHTML = (s = '') => String(s).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const dateParts = iso => { const d = new Date(iso); return { year: d.getFullYear(), month: d.getMonth() + 1, day: d.getDate() }; };
  const excerpt = text => String(text || '').replace(/\s+/g, ' ').trim().slice(0, 86);
  const postHref = p => `post.html?id=${encodeURIComponent(p.slug)}`;

  const grouped = posts.reduce((acc, post) => {
    const { year, month } = dateParts(post.posted_at);
    const key = `${year}-${month}`;
    (acc[key] ||= { year, month, posts: [] }).posts.push(post);
    return acc;
  }, {});
  const groups = Object.values(grouped).sort((a,b) => b.year-a.year || b.month-a.month);
  const years = [...new Set(groups.map(g => g.year))];
  yearNav.innerHTML = `<button class="year-pill active" data-year="all">全部</button>` + years.map(y => `<button class="year-pill" data-year="${y}">${y}</button>`).join('');

  function renderTimeline(year = 'all') {
    const visible = year === 'all' ? groups : groups.filter(g => String(g.year) === year);
    timeline.innerHTML = visible.map(group => `
      <section class="month-group" id="month-${group.year}-${group.month}">
        <div class="month-heading"><span>${String(group.month).padStart(2,'0')}</span><small>${group.year}</small></div>
        <div class="month-posts">${group.posts.map((p, i) => {
          const d = dateParts(p.posted_at), image = p.images && p.images[0];
          return `<a class="timeline-card ${image ? '' : 'text-only'}" href="${postHref(p)}" style="--delay:${Math.min(i,8)*35}ms">
            <span class="node-dot"></span>
            <div class="card-date">${String(d.day).padStart(2,'0')}<small>${['日','一','二','三','四','五','六'][new Date(p.posted_at).getDay()]}</small></div>
            ${image ? `<img src="assets/food/${encodeURIComponent(p.slug)}/${encodeURIComponent(image)}" alt="" loading="lazy">` : ''}
            <div class="card-copy"><h2>${escapeHTML(p.title || excerpt(p.content))}</h2><p>${escapeHTML(excerpt(p.content))}</p><span class="card-meta">${escapeHTML(p.author || '匿名')} · ${p.engagement?.likes || 0} 赞</span></div>
            <span class="arrow">↗</span>
          </a>`;
        }).join('')}</div>
      </section>`).join('');
  }
  renderTimeline();

  yearNav.addEventListener('click', e => {
    const button = e.target.closest('button'); if (!button) return;
    yearNav.querySelectorAll('button').forEach(b => b.classList.toggle('active', b === button));
    renderTimeline(button.dataset.year);
  });

  if (!reduceMotion) document.documentElement.classList.add('motion');
})();
