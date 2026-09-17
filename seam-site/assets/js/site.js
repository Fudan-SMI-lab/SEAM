/* ==========================================================================
   SEAM 介绍站 — 通用交互
   ========================================================================== */
(function () {
  'use strict';

  /* ---------------------------------------------------- header 滚动状态 */
  var header = document.querySelector('.site-header');
  function onScroll() {
    if (!header) return;
    header.classList.toggle('scrolled', window.scrollY > 8);
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* ---------------------------------------------------- 移动端导航 */
  var navToggle = document.querySelector('.nav-toggle');
  var nav = document.querySelector('.nav');
  if (navToggle && nav) {
    navToggle.addEventListener('click', function () {
      var open = nav.classList.toggle('open');
      navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    nav.addEventListener('click', function (e) {
      if (e.target.closest('a')) nav.classList.remove('open');
    });
    document.addEventListener('click', function (e) {
      if (!nav.contains(e.target) && !navToggle.contains(e.target)) nav.classList.remove('open');
    });
  }

  /* ---------------------------------------------------- 当前页高亮 */
  var here = location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('.nav-link').forEach(function (a) {
    var href = a.getAttribute('href') || '';
    if (href === here || (here === '' && href === 'index.html')) a.classList.add('active');
  });

  /* ---------------------------------------------------- 滚动进场 */
  var revealEls = document.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        var el = en.target;
        var delay = parseFloat(el.dataset.delay || '0');
        setTimeout(function () { el.classList.add('in'); }, delay * 1000);
        io.unobserve(el);
      });
    }, { threshold: 0.1, rootMargin: '0px 0px -40px 0px' });
    revealEls.forEach(function (el) { io.observe(el); });
  } else {
    revealEls.forEach(function (el) { el.classList.add('in'); });
  }

  /* ---------------------------------------------------- 数字滚动 */
  function animateCount(el) {
    var target = parseFloat(el.dataset.count);
    if (isNaN(target)) return;
    var suffix = el.dataset.suffix || '';
    var prefix = el.dataset.prefix || '';
    var decimals = (el.dataset.count.split('.')[1] || '').length;
    var dur = 900, t0 = performance.now();
    function frame(now) {
      var p = Math.min((now - t0) / dur, 1);
      var eased = 1 - Math.pow(1 - p, 3);
      var v = (target * eased).toFixed(decimals);
      el.textContent = prefix + Number(v).toLocaleString('zh-CN') + suffix;
      if (p < 1) requestAnimationFrame(frame);
      else el.textContent = prefix + target.toLocaleString('zh-CN') + suffix;
    }
    requestAnimationFrame(frame);
  }
  var countEls = document.querySelectorAll('[data-count]');
  if ('IntersectionObserver' in window && countEls.length) {
    var io2 = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        animateCount(en.target);
        io2.unobserve(en.target);
      });
    }, { threshold: 0.4 });
    countEls.forEach(function (el) { io2.observe(el); });
  } else {
    countEls.forEach(function (el) {
      el.textContent = (el.dataset.prefix || '') + el.dataset.count + (el.dataset.suffix || '');
    });
  }

  /* ---------------------------------------------------- 进度条进场 */
  function growBars(root) {
    (root || document).querySelectorAll('[data-width]').forEach(function (el) {
      el.style.width = el.dataset.width;
    });
    (root || document).querySelectorAll('[data-grow]').forEach(function (el) {
      el.style.flexGrow = el.dataset.grow;
    });
  }
  if ('IntersectionObserver' in window) {
    var io3 = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        growBars(en.target);
        io3.unobserve(en.target);
      });
    }, { threshold: 0.25 });
    document.querySelectorAll('.bars-block, [data-bars]').forEach(function (b) { io3.observe(b); });
  } else {
    growBars();
  }

  /* ---------------------------------------------------- 代码复制 */
  document.querySelectorAll('[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var sel = btn.dataset.copy;
      var src = sel ? document.querySelector(sel) : null;
      var text = src ? src.innerText.replace(/\u00a0/g, ' ') : btn.dataset.copyText || '';
      var done = function () {
        var old = btn.innerHTML;
        btn.classList.add('done');
        btn.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> 已复制';
        setTimeout(function () { btn.classList.remove('done'); btn.innerHTML = old; }, 1900);
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done).catch(fallback);
      } else { fallback(); }
      function fallback() {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); done(); } catch (e) { /* 忽略 */ }
        document.body.removeChild(ta);
      }
    });
  });

  /* ---------------------------------------------------- FAQ 手风琴 */
  document.querySelectorAll('.faq-q').forEach(function (q) {
    q.addEventListener('click', function () {
      var item = q.closest('.faq-item');
      var body = item.querySelector('.faq-a');
      var open = item.classList.contains('open');
      var group = item.closest('.faq');
      if (group && group.dataset.single !== 'false') {
        group.querySelectorAll('.faq-item.open').forEach(function (o) {
          o.classList.remove('open');
          o.querySelector('.faq-a').style.maxHeight = '';
        });
      }
      if (!open) {
        item.classList.add('open');
        body.style.maxHeight = body.scrollHeight + 'px';
      }
    });
  });

  /* ---------------------------------------------------- 文档侧边栏切换 */
  var docsNav = document.querySelector('.docs-nav');
  if (docsNav) {
    var panels = document.querySelectorAll('.doc-panel');
    function showDoc(id) {
      var found = false;
      panels.forEach(function (p) {
        var on = p.id === id;
        p.classList.toggle('active', on);
        if (on) found = true;
      });
      if (!found) return false;
      docsNav.querySelectorAll('a').forEach(function (a) {
        a.classList.toggle('active', a.dataset.doc === id);
      });
      return true;
    }
    docsNav.addEventListener('click', function (e) {
      var a = e.target.closest('a[data-doc]');
      if (!a) return;
      e.preventDefault();
      var id = a.dataset.doc;
      if (showDoc(id)) {
        history.replaceState(null, '', '#' + id);
        var top = document.querySelector('.site-header').offsetHeight + 20;
        window.scrollTo({ top: document.querySelector('.docs-layout').offsetTop - top, behavior: 'smooth' });
      }
    });
    var initial = decodeURIComponent((location.hash || '').replace('#', ''));
    if (!initial || !showDoc(initial)) showDoc('doc-intro');
    window.addEventListener('hashchange', function () {
      var h = decodeURIComponent((location.hash || '').replace('#', ''));
      if (h) showDoc(h);
    });
  }

  /* ---------------------------------------------------- FAQ 展开后重算高度 */
  window.addEventListener('resize', function () {
    document.querySelectorAll('.faq-item.open .faq-a').forEach(function (b) {
      b.style.maxHeight = b.scrollHeight + 'px';
    });
  });

  /* ---------------------------------------------------- 年份 */
  document.querySelectorAll('[data-year]').forEach(function (el) {
    el.textContent = new Date().getFullYear();
  });
})();
