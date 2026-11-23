"""
uikit.py: the phone layout and the "share this card" feature, shared by all four pages
(Statics, Videos, Weekly report, Monthly report).

Phone (<= 700px wide):
  * every category's cards become ONE swipeable row (scroll-snap) with a "3 / 10" counter, instead of a
    single column of ~800px-tall cards that has to be scrolled through
  * a sticky chip bar jumps between the categories of the tab you are on
  * the tab bars scroll sideways instead of overflowing
  * a transparent shield sits over each ad preview, because an embedded iframe swallows touch events: without
    it a swipe that starts on the preview would scroll the iframe, not the row. Tap the preview once to use it
    (play a video, tap the CTA); it re-arms as soon as you swipe away.
Desktop is unchanged.

Share: every card gets a small share button. It copies (desktop) or opens the phone's share sheet with a link
to that card ON THE DASHBOARD:   <page>#ad=<ad_id>&p=<pane>
Opening the link switches to the right tab, scrolls to the card (swiping the row on a phone) and highlights it.
No Meta call is involved, and nothing in the link expires; the preview inside is refreshed by each daily run.
"""
from html import escape

SHARE_SVG = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7"/>'
             '<path d="M16 6l-4-4-4 4"/><path d="M12 2v13"/></svg>')


def share_button(ad):
    """The share button for a card. Empty when the ad has no id (older data)."""
    ad_id = (ad or {}).get("ad_id")
    if not ad_id:
        return ""
    return (f'<button type="button" class="shr" data-ad="{escape(str(ad_id), quote=True)}" '
            f'aria-label="Share link to this card" title="Share this card">{SHARE_SVG}</button>')


UI_CSS = """
/* ── uikit: share button, highlight, toast ── */
.mm{position:relative;}
.mm .mn{padding-right:40px;}
.shr{position:absolute;top:6px;right:7px;width:32px;height:32px;display:flex;align-items:center;justify-content:center;
  border:1px solid #2a2a2a;border-radius:50%;background:#111;color:#9a9a9a;cursor:pointer;padding:0;}
.shr:hover{color:#e8e8e8;border-color:#555;}
.shr:focus-visible{outline:none;color:#e8e8e8;box-shadow:0 0 0 2px #3b82f6;}
.shr svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;pointer-events:none;}
.uk-flash{outline:2px solid #3b82f6;outline-offset:-2px;animation:ukflash 3.4s ease-out 1;}
@keyframes ukflash{0%,70%{box-shadow:0 0 0 5px rgba(59,130,246,.55);}100%{box-shadow:0 0 0 0 rgba(59,130,246,0);}}
@media (prefers-reduced-motion:reduce){.uk-flash{animation:none;}#uk-toast{transition:none;}}
#uk-toast{position:fixed;left:50%;bottom:22px;transform:translate(-50%,20px);background:#e8e8e8;color:#0c0c0c;padding:10px 16px;
  border-radius:8px;font:600 12.5px Inter,sans-serif;opacity:0;pointer-events:none;transition:opacity .2s,transform .2s;z-index:1000;max-width:90vw;text-align:center;}
#uk-toast.on{opacity:1;transform:translate(-50%,0);}
.uk-shield,.uk-count,#uk-chips{display:none;}
.uk-phone{display:none;}

/* ── uikit: phone layout ── */
@media (max-width:700px){
  :root{--uk-prev-h:min(66vh,600px);}
  html{overflow-x:hidden;}
  body{overflow-x:clip;}   /* clip, not hidden: hidden turns body into a scroll container and breaks position:sticky */
  .hdr{padding:14px 14px 0;}
  .hdr-row{flex-wrap:wrap;gap:8px;}
  .cover{padding:22px 16px;}
  .cover-title{font-size:24px;}
  .stats,.cover-stats{gap:12px 18px;padding:12px 14px;}
  .tab-content,.inner-content{padding:14px 14px;}
  .footer{padding:16px 14px;}

  .tabs,.format-tabs,.inner-tabs{overflow-x:auto;flex-wrap:nowrap;padding:0 8px;scrollbar-width:none;-webkit-overflow-scrolling:touch;}
  .tabs::-webkit-scrollbar,.format-tabs::-webkit-scrollbar,.inner-tabs::-webkit-scrollbar{display:none;}
  .tab,.ftab,.itab{flex:0 0 auto;white-space:nowrap;min-height:44px;padding:0 14px;display:flex;align-items:center;}

  .sec{margin-bottom:20px;scroll-margin-top:62px;}
  .sec-hdr{flex-direction:column;align-items:flex-start;gap:4px;}

  /* one swipeable row per category */
  .cgrid{display:flex;grid-template-columns:none;overflow-x:auto;overflow-y:hidden;scroll-snap-type:x mandatory;gap:12px;
    padding:2px 14px 8px;margin-left:-14px;margin-right:-14px;scrollbar-width:none;-webkit-overflow-scrolling:touch;
    overscroll-behavior-x:contain;align-items:flex-start;}
  .cgrid::-webkit-scrollbar{display:none;}
  .cgrid>*{flex:0 0 86%;max-width:86%;min-width:0;scroll-snap-align:center;scroll-snap-stop:always;}
  .mpw,.static-preview,.video-preview,.no-prev{height:var(--uk-prev-h);position:relative;}
  .mpw iframe,.static-preview iframe,.video-preview iframe{height:100%;}
  .uk-phone{display:block;}
  .uk-desk{display:none;}
  .uk-count{display:block;text-align:center;font:600 11px "JetBrains Mono",monospace;color:#8a8a8a;margin:0 0 16px;}

  /* iframes swallow touches: the shield lets a swipe that starts on the preview move the row */
  .uk-shield{display:flex;position:absolute;left:0;top:0;right:0;bottom:0;z-index:4;align-items:flex-end;justify-content:center;padding-bottom:10px;}
  .uk-shield span{font:600 10px Inter,sans-serif;color:#d0d0d0;background:rgba(0,0,0,.62);padding:5px 10px;border-radius:12px;pointer-events:none;}
  .mpw.uk-live .uk-shield{display:none;}

  /* category chips */
  #uk-chips{display:flex;gap:8px;overflow-x:auto;position:sticky;top:0;z-index:55;background:rgba(12,12,12,.96);padding:8px 14px;
    border-bottom:1px solid #1e1e1e;scrollbar-width:none;-webkit-overflow-scrolling:touch;}
  #uk-chips:empty{display:none;}
  #uk-chips::-webkit-scrollbar{display:none;}
  #uk-chips button{flex:0 0 auto;min-height:36px;padding:0 14px;border-radius:18px;border:1px solid #2a2a2a;background:#111;color:#bbb;
    font:500 12px Inter,sans-serif;white-space:nowrap;cursor:pointer;}
  #uk-chips button.on{background:#3b82f6;border-color:#3b82f6;color:#fff;}

  .filter-bar{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;}
  .filter-bar::-webkit-scrollbar{display:none;}
  .filter-btn{flex:0 0 auto;white-space:nowrap;min-height:36px;padding:0 14px;}
  .shr{width:38px;height:38px;top:4px;}
}
"""


UI_JS = r"""
(function () {
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var phone = window.matchMedia('(max-width: 700px)');
  var touch = window.matchMedia('(pointer: coarse)');
  var smooth = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
  var esc = function (s) { return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&'); };

  /* ---------- toast + copy ---------- */
  function toast(msg) {
    var t = $('#uk-toast');
    if (!t) { t = document.createElement('div'); t.id = 'uk-toast'; t.setAttribute('role', 'status'); t.setAttribute('aria-live', 'polite'); document.body.appendChild(t); }
    t.textContent = msg; t.classList.add('on');
    clearTimeout(t._h); t._h = setTimeout(function () { t.classList.remove('on'); }, 2800);
  }
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (ok, no) {
      var ta = document.createElement('textarea'); ta.value = text; ta.setAttribute('readonly', '');
      ta.style.cssText = 'position:fixed;top:-1000px;opacity:0'; document.body.appendChild(ta); ta.select();
      var done = false; try { done = document.execCommand('copy'); } catch (e) {}
      document.body.removeChild(ta); done ? ok() : no();
    });
  }

  /* ---------- share a card ---------- */
  function paneOf(el) { var p = el.closest('#mv-cur, #mv-prev, #mv-only, .tab-content, .inner-content, .tab-pane'); return p ? p.id : ''; }
  function cardLink(id, pane) {
    return location.href.split('#')[0] + '#ad=' + encodeURIComponent(id) + (pane ? '&p=' + encodeURIComponent(pane) : '');
  }
  function share(btn) {
    var card = btn.closest('.mc'), n = card && $('.mn', card);
    var name = n ? n.textContent.trim() : 'Ad';
    var url = cardLink(btn.getAttribute('data-ad'), paneOf(btn));
    var copied = function () { toast('Link to this card copied'); };
    if (navigator.share && touch.matches) {
      navigator.share({ title: name, text: name, url: url }).catch(function (err) {
        if (err && err.name === 'AbortError') return;
        copyText(url).then(copied, function () { window.prompt('Copy this link', url); });
      });
      return;
    }
    copyText(url).then(copied, function () { window.prompt('Copy this link', url); });
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.shr');
    if (b) { e.preventDefault(); e.stopPropagation(); share(b); }
  });

  /* ---------- open a shared card ---------- */
  function parseHash() {
    var o = {}; location.hash.replace(/^#/, '').split('&').forEach(function (kv) {
      var i = kv.indexOf('='); if (i > 0) { try { o[kv.slice(0, i)] = decodeURIComponent(kv.slice(i + 1)); } catch (e) {} }
    });
    return o;
  }
  function revealPane(el) {                       /* use each page's own tab functions to un-hide the card */
    var tc = el.closest('.tab-content');
    if (tc && typeof switchTab === 'function') { var i = $$('.tab-content').indexOf(tc); if (i >= 0) switchTab(i); }
    var tp = el.closest('.tab-pane');
    if (tp && typeof switchFormat === 'function') { var m = /^fmt(\d+)$/.exec(tp.id || ''); if (m) switchFormat(+m[1]); }
    var ic = el.closest('.inner-content');
    if (ic && typeof switchInner === 'function') { var q = /^([sv])-inner(\d+)$/.exec(ic.id || ''); if (q) switchInner(q[1] === 's' ? 0 : 1, +q[2]); }
    var mv = el.closest('#mv-cur, #mv-prev, #mv-only');
    if (mv && typeof mvShow === 'function') mvShow(mv.id.replace('mv-', ''));
    var sec = el.closest('.sec');
    if (sec && sec.style.display === 'none') { var all = $('.filter-btn', tc || tp || document); if (all) all.click(); }
  }
  var flashT;
  function showAd(id, paneId) {
    var btns = $$('.shr[data-ad="' + esc(id) + '"]');
    if (!btns.length) { toast('This ad is no longer in the current view'); return false; }
    var pick = btns[0];
    if (paneId) { var hit = btns.filter(function (b) { return b.closest('#' + esc(paneId)); })[0]; if (hit) pick = hit; }
    var card = pick.closest('.mc'); if (!card) return false;
    revealPane(card);
    setTimeout(function () {
      card.scrollIntoView({ behavior: smooth, block: 'center', inline: 'center' });
      $$('.uk-flash').forEach(function (x) { x.classList.remove('uk-flash'); });
      void card.offsetWidth; card.classList.add('uk-flash');
      clearTimeout(flashT); flashT = setTimeout(function () { card.classList.remove('uk-flash'); }, 3600);
    }, 80);
    return true;
  }
  function route() { var h = parseHash(); if (h.ad) showAd(h.ad, h.p); }
  window.addEventListener('hashchange', route);
  window.ukShowAd = showAd;

  /* ---------- phone: a counter under every swipeable row ---------- */
  function setupRows() {
    $$('.cgrid').forEach(function (g) {
      if (g._uk) return; g._uk = 1;
      var n = g.children.length; if (n < 2) return;
      var c = document.createElement('div'); c.className = 'uk-count';
      g.parentNode.insertBefore(c, g.nextSibling);
      function upd() {
        var kids = g.children, best = 0, d = 1e9, mid = g.scrollLeft + g.clientWidth / 2;
        for (var i = 0; i < kids.length; i++) { var cx = kids[i].offsetLeft - g.offsetLeft + kids[i].offsetWidth / 2; var dd = Math.abs(cx - mid); if (dd < d) { d = dd; best = i; } }
        c.textContent = (best + 1) + ' / ' + n;
      }
      g.addEventListener('scroll', function () {
        if (g._raf) return;
        g._raf = requestAnimationFrame(function () { g._raf = 0; upd(); rearm(); });
      }, { passive: true });
      upd();
    });
  }

  /* ---------- phone: tap-to-interact shield over each preview ---------- */
  function rearm() { $$('.mpw.uk-live').forEach(function (x) { x.classList.remove('uk-live'); }); }
  function setupShields() {
    $$('.mpw').forEach(function (w) {
      if (w._uk || !w.querySelector('iframe')) return; w._uk = 1;
      var s = document.createElement('div'); s.className = 'uk-shield'; s.innerHTML = '<span>Tap to interact</span>';
      s.addEventListener('click', function () { rearm(); w.classList.add('uk-live'); });
      w.appendChild(s);
    });
  }

  /* ---------- phone: category chips for the tab you are on ---------- */
  var chipObs;
  function activeScope() {
    return $('.tab-pane.active .inner-content.active') || $('.tab-content.active') || document.body;
  }
  function buildChips() {
    var bar = $('#uk-chips');
    if (!bar) {
      bar = document.createElement('div'); bar.id = 'uk-chips'; bar.setAttribute('role', 'navigation'); bar.setAttribute('aria-label', 'Jump to category');
      var anchor = $('.tab-content') || $('.tab-pane') || document.body.firstElementChild;
      if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(bar, anchor);
    }
    bar.innerHTML = ''; if (chipObs) chipObs.disconnect();
    var scope = activeScope();
    if (scope.querySelector('.filter-bar')) return;               /* All Ads tabs already have their own filter */
    var secs = $$('.sec', scope).filter(function (s) { return !s.classList.contains('grp-hdr') && $('.sec-t', s) && s.offsetParent !== null; });
    if (secs.length < 2) return;
    var chips = secs.map(function (s) {
      var b = document.createElement('button'); b.type = 'button'; b.textContent = $('.sec-t', s).textContent.trim();
      b.addEventListener('click', function () { s.scrollIntoView({ behavior: smooth, block: 'start' }); });
      bar.appendChild(b); return b;
    });
    if ('IntersectionObserver' in window) {
      chipObs = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          var i = secs.indexOf(en.target); if (i < 0) return;
          chips.forEach(function (c, j) { c.classList.toggle('on', i === j); });
          if (phone.matches) { var c = chips[i]; var l = c.offsetLeft - (bar.clientWidth - c.offsetWidth) / 2; bar.scrollTo ? bar.scrollTo({ left: l, behavior: smooth }) : (bar.scrollLeft = l); }
        });
      }, { rootMargin: '-70px 0px -60% 0px', threshold: 0 });
      secs.forEach(function (s) { chipObs.observe(s); });
    }
  }
  document.addEventListener('click', function (e) {
    if (e.target.closest && e.target.closest('.tab, .ftab, .itab, .filter-btn, #mvb-cur, #mvb-prev')) setTimeout(function () { buildChips(); setupRows(); setupShields(); }, 90);
  });

  function init() {
    setupRows(); setupShields(); buildChips();
    setTimeout(route, 150);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
"""
