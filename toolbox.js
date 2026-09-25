"use strict";
(() => {
  const paths = {
    box:'<path d="m12 3 9 5v9l-9 5-9-5V8z"/><path d="m3 8 9 5 9-5M12 13v9M7.5 5.5l9 5"/>',
    grid:'<rect x="3" y="3" width="7" height="7" rx="1.8"/><rect x="14" y="3" width="7" height="7" rx="1.8"/><rect x="3" y="14" width="7" height="7" rx="1.8"/><rect x="14" y="14" width="7" height="7" rx="1.8"/>',
    star:'<path d="m12 3 2.8 5.8 6.4.9-4.6 4.5 1.1 6.4-5.7-3-5.7 3 1.1-6.4L2.8 9.7l6.4-.9z"/>',
    clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
    activity:'<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    folder:'<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/><path d="M3 9h18"/>',
    search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    book:'<path d="M12 5C9 3 5 3 3 4v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1v15"/>',
    shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    drive:'<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M3 14h18M7 16.5h.1M10 16.5h.1"/>',
    sparkles:'<path d="m12 3 2.7 6.3L21 12l-6.3 2.7L12 21l-2.7-6.3L3 12l6.3-2.7zM20 2v4M18 4h4"/>',
    'arrow-up-right':'<path d="M6 18 18 6M6 6h12v12"/>',
    arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',
    play:'<path d="m8 4 12 8-12 8z"/>',
    code:'<path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-14-2 16"/>',
    wand:'<path d="m4 20 12-12 4 4L8 24M5 4v4M3 6h4M17 2v4M15 4h4M20 18v4M18 20h4" transform="translate(0 -2)"/>',
    list:'<path d="M8 5h13M8 12h13M8 19h13M3 5h.1M3 12h.1M3 19h.1"/>',
    refresh:'<path d="M20 8a8 8 0 1 0 .5 7M20 3v5h-5"/>',
    film:'<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3 9h18M7 4l3 5m4-5 3 5m-7 3 5 3-5 3z"/>',
    'file-play':'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6m-10 3 5 3-5 3z"/>',
    file:'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M8 13h8M8 17h5"/>',
    send:'<path d="m22 2-7 20-4-9L2 9zM11 13 22 2"/>',
    flask:'<path d="M9 3h6M10 3v6L4 19a1.4 1.4 0 0 0 1.2 2h13.6a1.4 1.4 0 0 0 1.2-2L14 9V3M8 14h8M10 17h.1"/>',
    cube:'<path d="m12 2 9 5v10l-9 5-9-5V7zM3 7l9 5 9-5M12 12v10M7.5 4.5l9 5v4"/>',
    palette:'<path d="M12 3a9 9 0 1 0 0 18h1a2 2 0 0 0 1-3.7 1.6 1.6 0 0 1 1-2.8h2.5A3.5 3.5 0 0 0 21 11 9 9 0 0 0 12 3z"/><path d="M7 9h.1M10 6h.1M15 7h.1M6 13h.1" stroke-width="3"/>'
  };
  const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.box}</svg>`;
  document.querySelectorAll('[data-icon]').forEach(el => el.innerHTML = icon(el.dataset.icon));
  const readSaved = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } };
  const save = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { toast('浏览器未允许保存偏好，本次操作仍然有效'); } };
  const savedFavorites = readSaved('toolbox-favorites', []);
  let favorites = new Set(Array.isArray(savedFavorites) ? savedFavorites.filter(id => typeof id === 'string') : []);
  let recent = readSaved('toolbox-recent', []);
  if (!Array.isArray(recent)) recent = [];
  let catalog = null, category = 'all', view = 'all';
  let layout = readSaved('toolbox-layout', 'grid') === 'list' ? 'list' : 'grid';
  const pending = new Set();
  const labels = {toolbox:'工具箱',overview:'工作总览',projects:'项目空间',search:'文档搜索',knowledge:'知识库',security:'安全检查'};
  const remember = id => { recent = [id, ...recent.filter(x => x !== id)].slice(0,30); save('toolbox-recent',recent); };
  async function request(url, body) {
    const response = await fetch(url, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok || result.error) throw new Error(result.error || '工具箱暂时没有响应');
    return result;
  }
  function renderCategories() {
    if (!catalog) return;
    $('tool-categories').innerHTML = [{id:'all',name:'全部'}, ...catalog.categories].map(c => `<button data-category="${esc(c.id)}" class="${category === c.id ? 'on' : ''}" aria-pressed="${category === c.id}">${esc(c.name)}</button>`).join('');
  }
  function render() {
    if (!catalog) return;
    const query = $('tool-search').value.trim().toLocaleLowerCase();
    let tools = catalog.tools.filter(t => (category === 'all' || t.category === category) && (view !== 'favorites' || favorites.has(t.id)) && (view !== 'recent' || recent.includes(t.id)));
    const terms = query.split(/\s+/).filter(Boolean);
    tools = tools.filter(t => terms.every(term => [t.name,t.description,...(t.tags||[]),t.guide?.purpose||'',...(t.guide?.features||[]),catalog.categories.find(c => c.id === t.category)?.name||''].join(' ').toLocaleLowerCase().includes(term)));
    if (view === 'recent') tools.sort((a,b) => recent.indexOf(a.id)-recent.indexOf(b.id));
    $('tools-title').firstChild.textContent = view === 'favorites' ? '留给常用的好工具。' : view === 'recent' ? '继续上一次的灵感。' : '为创造，准备就绪。';
    $('tools-subtitle').textContent = view === 'favorites' ? '点击星标，把顺手的工具留在这里。' : view === 'recent' ? '你从工具箱打开过的工具，按最近使用排列。' : '熟悉的工具，全新的秩序。';
    $('tools-count').textContent = tools.length;
    $('tool-nav-count').textContent = catalog.tools.length;
    $('hero-count').textContent = `${catalog.tools.length} 个工具 · ${catalog.categories.length} 个分类`;
    $('tool-location').textContent = catalog.root.replaceAll('/','\\');
    $('tool-grid').innerHTML = tools.map((t,i) => {
      const pinned = favorites.has(t.id), busy = pending.has(t.id);
      const blocked = t.service_state === 'conflict' || t.service_state === 'unknown';
      const status = !t.available ? '路径待检查' : busy ? '正在启动' : t.running ? '后台运行中' : t.service_state === 'conflict' ? '端口被其他服务占用' : t.service_state === 'unknown' ? '服务身份待确认' : t.status_label || (t.url ? '未运行' : t.launchable ? '本地应用' : '命令行工具');
      const statusHelp = t.running ? '已确认对应工具的后台服务可连接，不代表你在工具箱中启动过它。' : t.service_state === 'conflict' ? '这个地址上运行的是其他服务。为避免误开网页或启动冲突，暂时只提供文件夹入口。' : t.service_state === 'unknown' ? '端口有响应，但暂时无法确认是此工具。点击刷新状态可以重新检查。' : '';
      const action = !blocked && (t.launchable || t.running) ? 'launch' : 'folder';
      const color = /^#[0-9a-f]{6}$/i.test(t.color) ? t.color : '#9a89ba';
      return `<article class="tool-card ${layout === 'list' ? 'list-card' : ''}" style="--tool-color:${color};animation-delay:${Math.min(i,8)*35}ms" data-tool="${esc(t.id)}">
        <div class="tool-card-top"><div class="app-icon">${icon(t.icon)}</div><button class="favorite-btn ${pinned ? 'active' : ''}" data-favorite="${esc(t.id)}" aria-label="${pinned ? '取消收藏' : '收藏'} ${esc(t.name)}" title="${pinned ? '取消收藏' : '收藏'}" aria-pressed="${pinned}">${icon('star')}</button></div>
        <div class="tool-name-row"><h3><button class="tool-title-button" data-tool-details="${esc(t.id)}" title="查看 ${esc(t.name)} 的用途与用法">${esc(t.name)}</button></h3>${t.featured ? '<span class="tool-tag">常用</span>' : ''}</div>
        <p class="tool-description">${esc(t.description)}</p>
        <div class="tool-card-bottom"><span class="tool-status ${t.running ? 'running' : !t.available || blocked ? 'missing' : ''}" title="${esc(statusHelp)}"><i></i>${esc(status)}</span><div class="tool-card-actions"><button class="tool-folder" data-tool-details="${esc(t.id)}" title="${esc(t.name)}：用途与用法" aria-label="${esc(t.name)}：用途与用法">${icon('info')}</button><button class="tool-folder" data-tool-action="folder" data-id="${esc(t.id)}" title="打开 ${esc(t.name)} 文件夹" aria-label="打开 ${esc(t.name)} 文件夹" ${!t.available?'disabled':''}>${icon('folder')}</button><button class="tool-launch" data-tool-action="${action}" data-id="${esc(t.id)}" ${busy || !t.available ? 'disabled' : ''}>${busy ? '启动中…' : action === 'folder' ? '打开文件夹' : t.open_mode === 'desktop' ? '打开桌面' : t.launch_mode === 'interactive' ? '打开启动器' : t.running ? '打开工具' : '启动工具'}${icon('arrow-up-right')}</button></div></div>
      </article>`;
    }).join('') || `<div class="tool-empty glass"><h3>${query || category !== 'all' ? '没有找到匹配的工具' : view === 'favorites' ? '把好用的工具，留在手边。' : '还没有使用记录'}</h3><p>${query || category !== 'all' ? '试试其他关键词，或查看全部分类。' : view === 'favorites' ? '点击工具卡片右上角的星标，即可收藏。' : '从工具箱打开一个工具，就会出现在这里。'}</p><button class="btn" data-reset-tools>查看全部工具 ${icon('arrow')}</button></div>`;
    document.querySelectorAll('[data-layout]').forEach(b => { b.classList.toggle('on',b.dataset.layout === layout); b.setAttribute('aria-pressed',b.dataset.layout === layout); });
  }
  async function refresh(quiet=false) {
    try { catalog = await request('/api/tools'); renderCategories(); render(); return catalog; }
    catch(error) {
      if (!quiet) {
        if (!catalog) $('tool-grid').innerHTML = `<div class="tool-empty glass"><h3>工具箱暂时未能加载</h3><p>${esc(error.message)}</p><button class="btn" data-retry-tools>重新加载</button></div>`;
        toast(error.message);
      }
      return null;
    }
  }
  function openToolPage(url, id) { window.open(url, 'toolbox-'+id, 'noopener'); }
  function showDetails(id) {
    const tool = catalog?.tools.find(t => t.id === id); if(!tool) return;
    const guide = tool.guide || {};
    const list = (title,items,ordered=false) => !items?.length ? '' : `<section class="tool-guide-section"><h3>${esc(title)}</h3><${ordered?'ol':'ul'}>${items.map(item=>`<li>${esc(item)}</li>`).join('')}</${ordered?'ol':'ul'}></section>`;
    const blocked = ['conflict','unknown'].includes(tool.service_state);
    const canLaunch = !blocked && (tool.launchable || tool.running);
    openDrawer(`${esc(tool.name)} · 用途与用法`, `<div class="tool-guide">
      <p class="tool-purpose">${esc(guide.purpose || tool.description)}</p>
      <div class="tool-guide-actions"><button class="btn primary" data-guide-action="${canLaunch?'launch':'folder'}" data-id="${esc(id)}" ${!tool.available?'disabled':''}>${!canLaunch?'打开工具文件夹':tool.open_mode==='desktop'?'打开桌面应用':tool.launch_mode==='interactive'?'打开交互启动器':'启动 / 打开工具'}</button>${guide.agent_readme?`<button class="btn" data-act="preview" data-v="${esc(guide.agent_readme)}">Agent 使用说明</button>`:''}${guide.readme?`<button class="btn" data-act="preview" data-v="${esc(guide.readme)}">查看完整说明</button>`:''}<button class="btn" data-guide-action="folder" data-id="${esc(id)}" ${!tool.available?'disabled':''}>打开文件夹</button></div>
      ${list('可以做什么',guide.features)}
      <div class="tool-guide-io"><section><h3>准备什么</h3><p>${esc(guide.inputs || '请参考完整使用说明。')}</p></section><section><h3>得到什么</h3><p>${esc(guide.outputs || '请参考完整使用说明。')}</p></section></div>
      ${list('开始使用',guide.steps,true)}${list('使用前需要',guide.requirements)}
      ${list('边界与待配置项',guide.limitations)}${list('这次已完善',guide.fixes)}
      ${list('已验证的范围',guide.verification)}
      <div class="tool-guide-path"><strong>工具位置</strong><span>${esc(tool.path)}</span></div>
    </div>`);
  }
  async function runTool(id, action) {
    const tool = catalog?.tools.find(t => t.id === id);
    if (!tool || pending.has(id)) return;
    if (action === 'launch' && tool.running && tool.url && tool.open_mode !== 'desktop') {
      openToolPage(tool.url,id); remember(id); if(view === 'recent') render(); return;
    }
    pending.add(id); render();
    try {
      const result = await request('/api/tools/action',{id,action});
      remember(id);
      if (action === 'folder') toast(tool.note || `已打开 ${tool.name} 文件夹`,tool.note ? 6000 : 2800);
      else if (result.interactive) { toast(`已打开 ${tool.name} 的交互窗口，请按窗口提示继续；关闭窗口即可退出。`,5500); }
      else if (result.desktop) { toast(`已发送 ${tool.name} 桌面打开请求`,3500); }
      else if (result.status === 'online') { toast(`${tool.name} 已就绪，点击“打开工具”进入`); }
      else if (result.url) {
        toast(`正在启动 ${tool.name}…`,4000);
        let ready = false;
        for (let i=0;i<12;i++) {
          await new Promise(resolve => setTimeout(resolve,1800));
          const data = await refresh(true);
          if (data?.tools.find(t => t.id === id)?.running) { ready = true; break; }
        }
        toast(ready ? `${tool.name} 已就绪，点击“打开工具”进入` : '启动请求已发送，服务尚未就绪。稍后刷新状态，或从文件夹查看启动日志。',5500);
      } else { toast(`已发送 ${tool.name} 启动请求`,3500); }
    } catch(error) { toast(error.message,5000); }
    finally { pending.delete(id); await refresh(true); render(); }
  }
  $('tool-grid').addEventListener('click',event => {
    const details = event.target.closest('[data-tool-details]');
    if(details) { showDetails(details.dataset.toolDetails); return; }
    const favorite = event.target.closest('[data-favorite]');
    if (favorite) { const id=favorite.dataset.favorite; favorites.has(id)?favorites.delete(id):favorites.add(id); save('toolbox-favorites',[...favorites]); render(); return; }
    const action = event.target.closest('[data-tool-action]');
    if (action) { runTool(action.dataset.id,action.dataset.toolAction); return; }
    if (event.target.closest('[data-retry-tools]')) refresh();
    if (event.target.closest('[data-reset-tools]')) {
      $('tool-search').value=''; category='all'; document.querySelector('#nav [data-view="all"]').click(); renderCategories(); render();
    }
  });
  document.addEventListener('click',event=>{const action=event.target.closest('[data-guide-action]');if(action)runTool(action.dataset.id,action.dataset.guideAction);});
  $('tool-categories').addEventListener('click',event => { const b=event.target.closest('[data-category]'); if(b) {category=b.dataset.category;renderCategories();render();} });
  $('tool-search').addEventListener('input',render);
  document.querySelectorAll('[data-layout]').forEach(b => b.onclick = () => {layout=b.dataset.layout;save('toolbox-layout',layout);render();});
  $('refreshTools').onclick=async () => { const b=$('refreshTools');b.disabled=true;await refresh();b.disabled=false; };
  $('openToolbox').onclick=async () => {try{await request('/api/tools/folder',{});toast('工具箱文件夹已打开');}catch(error){toast(error.message);}};
  $('nav').addEventListener('click',event => {
    const b=event.target.closest('button[data-tab]'); if(!b) return;
    $('page-label').textContent = labels[b.dataset.tab] || '工具箱';
    if(b.dataset.tab === 'toolbox') {view=b.dataset.view||'all';category='all';$('tool-search').value='';renderCategories();render();}
    if(b.dataset.tab === 'search') $('q').focus();
  });
  document.addEventListener('keydown',event => {
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k') {event.preventDefault();document.querySelector('#nav [data-view="all"]').click();$('tool-search').focus();}
  });
  // Specular highlights follow the pointer; motion can be paused independently.
  $('tool-grid').addEventListener('pointermove',event => {
    if(document.documentElement.dataset.motion==='off'||matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const card=event.target.closest('.tool-card');if(!card)return;
    const rect=card.getBoundingClientRect();card.style.setProperty('--mx',`${event.clientX-rect.left}px`);card.style.setProperty('--my',`${event.clientY-rect.top}px`);
  },{passive:true});
  let motion = readSaved('toolbox-motion',true);
  function applyMotion(){document.documentElement.dataset.motion=motion?'on':'off';$('motionBtn').setAttribute('aria-pressed',String(!motion));$('motionBtn').title=motion?'暂停动态效果':'开启动态效果';$('motionBtn').setAttribute('aria-label',$('motionBtn').title);}
  $('motionBtn').onclick=()=>{motion=!motion;save('toolbox-motion',motion);applyMotion();};applyMotion();
  $('toast').setAttribute('role','status');
  refresh();
  addEventListener('unhandledrejection',event => {toast(event.reason?.message || '操作未完成，请稍后重试',4500);});
})();
