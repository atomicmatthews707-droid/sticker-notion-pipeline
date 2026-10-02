"""
Look of Sticker Studio: "Clean as Outerspace". Dense obsidian glass panels over a space-nebula field, with an
anisotropic light streak that breathes (about 0.1 Hz) and slides with the cursor or device tilt.
Everything visual lives here so app.py only describes layout and behaviour.
"""

CSS = """
:root {
  --bg-deep-void: #05070a;
  --bg-space-nebula: #080b0f;
  --panel-glass-bg: rgba(12, 16, 24, 0.65);
  --panel-glass-border: rgba(255, 255, 255, 0.18);
  --aniso-specular-primary: rgba(180, 220, 255, 1.0);
  --text-specular-glow: 0 0 12px rgba(180, 220, 255, 0.6);
  --symbol-cyan-glow: 0 0 16px rgba(0, 255, 255, 0.8);
  --lock-glow: 0 0 20px rgba(0, 255, 255, 0.9);
  --txt: #e6eef8; --mut: #8b9bb0; --cyan: #00e5ff; --violet: #b9a7ff; --danger: #ff8aa0;
  /* moved live by the script below: where the light streak sits on every panel */
  --aniso-angle: 118deg; --aniso-shift: 0%;
}
html, body, .q-page, .nicegui-content { background: transparent !important; }
body {
  color: var(--txt); min-height: 100vh;
  background:
    radial-gradient(900px 600px at 15% 8%, rgba(80, 50, 160, .45), transparent 60%),
    radial-gradient(900px 700px at 92% 92%, rgba(20, 70, 150, .40), transparent 60%),
    radial-gradient(600px 400px at 60% 28%, rgba(120, 40, 140, .22), transparent 60%),
    var(--bg-deep-void) !important;
  background-attachment: fixed !important;
}
.nicegui-content { padding: 0 !important; }
.stars { position: fixed; inset: 0; pointer-events: none; z-index: 0; opacity: .55;
  background-image: radial-gradient(1px 1px at 20px 30px, #fff, transparent), radial-gradient(1px 1px at 140px 90px, #cfe, transparent),
    radial-gradient(1.5px 1.5px at 260px 170px, #fff, transparent), radial-gradient(1px 1px at 380px 40px, #bdf, transparent),
    radial-gradient(1px 1px at 90px 210px, #fff, transparent);
  background-size: 420px 260px; }

.studio { position: relative; z-index: 1; display: grid; grid-template-columns: minmax(380px, 440px) minmax(0, 1fr);
  gap: 16px; padding: 16px 20px 24px; width: 100%; max-width: 1900px; margin: 0 auto; align-items: start; }
.studio > .span-all { grid-column: 1 / -1; }
.lane { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.mid { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 16px; align-items: stretch; }
@media (max-width: 1100px) { .studio { grid-template-columns: 1fr; } .mid { grid-template-columns: 1fr; } }

/* deep obsidian glass, with the brushed-metal texture and the moving anisotropic streak */
.glass { position: relative; border-radius: 18px; background: var(--panel-glass-bg); border: 1px solid var(--panel-glass-border);
  backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px); overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, .6), inset 0 1px 0 rgba(255, 255, 255, .14); padding: 6px 4px 14px; }
.glass::before { content: ""; position: absolute; inset: 0; pointer-events: none; border-radius: inherit; z-index: 0;
  background:
    repeating-linear-gradient(100deg, rgba(255, 255, 255, .035) 0 1px, transparent 1px 4px),
    linear-gradient(var(--aniso-angle), transparent calc(30% + var(--aniso-shift)), rgba(180, 220, 255, .18) calc(46% + var(--aniso-shift)),
      rgba(220, 240, 255, .32) calc(50% + var(--aniso-shift)), rgba(180, 220, 255, .12) calc(54% + var(--aniso-shift)), transparent calc(70% + var(--aniso-shift)));
  animation: breathe 10s ease-in-out infinite; }
.glass::after { content: ""; position: absolute; inset: 0; pointer-events: none; border-radius: inherit; z-index: 0;
  background: linear-gradient(135deg, rgba(255, 255, 255, .22) 0%, rgba(255, 255, 255, .03) 38%, transparent 100%);
  -webkit-mask: linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0); padding: 1px; }
.glass > * { position: relative; z-index: 1; }
@keyframes breathe { 0%, 100% { opacity: 1; } 50% { opacity: .7; } }   /* 1.0 <-> 0.7 over 10 s = 0.1 Hz */
@media (prefers-reduced-motion: reduce) { .glass::before { animation: none; } }

.h2 { font-size: 12px; font-weight: 600; letter-spacing: .18em; text-transform: uppercase; color: var(--aniso-specular-primary);
  text-shadow: var(--text-specular-glow); padding: 12px 16px 6px; }
.mut { color: var(--mut); font-size: 12px; }
.pad { padding-left: 16px; padding-right: 16px; }

/* header */
.logo { font-size: 24px; font-weight: 700; letter-spacing: .04em; text-shadow: 0 0 16px rgba(0, 255, 255, .55); }
.logo b { color: var(--cyan); font-weight: 700; }
.stat .cap { display: block; font-size: 10px; letter-spacing: .16em; color: var(--mut); text-transform: uppercase; }
.stat .v { font-size: 28px; line-height: 1.1; font-weight: 700; text-shadow: var(--text-specular-glow); }
.stat.hot .v { color: var(--cyan); text-shadow: var(--symbol-cyan-glow); font-size: 32px; }
.stat .of { font-size: 14px; color: var(--mut); font-weight: 500; text-shadow: none; }

/* controls */
.q-field--outlined .q-field__control { background: rgba(0, 0, 0, .32); border-radius: 12px; }
.q-field--outlined .q-field__control:before { border-color: rgba(255, 255, 255, .18) !important; }
.q-field--outlined.q-field--focused .q-field__control:after { border-color: var(--cyan) !important; box-shadow: 0 0 14px rgba(0, 229, 255, .35); }
.q-field__native, .q-field__input, .q-field__label, .q-select__dropdown-icon { color: var(--txt) !important; }
.q-field__label { color: var(--mut) !important; }
.mono textarea { font-family: ui-monospace, Menlo, Consolas, monospace !important; font-size: 12px !important; color: #bcd0e6 !important; }
.q-btn.pill { border-radius: 999px; border: 1px solid rgba(255, 255, 255, .18); background: rgba(255, 255, 255, .06); color: var(--txt);
  letter-spacing: .06em; text-transform: none; font-size: 12.5px; padding: 6px 16px; }
.q-btn.pill:hover { background: rgba(255, 255, 255, .12); }
.q-btn.pill.pri { background: linear-gradient(180deg, rgba(0, 229, 255, .38), rgba(0, 140, 200, .38)); border-color: rgba(0, 229, 255, .7);
  box-shadow: 0 0 22px rgba(0, 229, 255, .42); font-weight: 600; }
.q-btn.pill.vio { border-color: rgba(185, 167, 255, .7); box-shadow: 0 0 16px rgba(185, 167, 255, .3); }
.q-btn.pill.neg { color: var(--danger); border-color: rgba(255, 120, 150, .5); }
.q-btn.pill.q-btn--disabled { opacity: .4 !important; box-shadow: none; }
.q-toggle__inner--truthy .q-toggle__track { background: rgba(0, 229, 255, .55) !important; opacity: 1; box-shadow: 0 0 12px rgba(0, 229, 255, .55); }
.q-toggle__inner--truthy .q-toggle__thumb:after { background: #fff !important; }
.q-toggle { font-size: 12.5px; width: 100%; }
.q-toggle__label { color: var(--txt); }
.q-btn-group.seg { border-radius: 999px; box-shadow: none; gap: 6px; }
.q-btn-group.seg .q-btn { border-radius: 999px !important; border: 1px solid rgba(255, 255, 255, .18); background: rgba(255, 255, 255, .05); color: var(--txt);
  text-transform: none; letter-spacing: .04em; font-size: 12.5px; padding: 6px 14px; }
.q-btn-group.seg .q-btn.bg-primary, .q-btn-group.seg .q-btn[aria-pressed="true"] { background: rgba(0, 229, 255, .22) !important; border-color: rgba(0, 229, 255, .8);
  box-shadow: 0 0 16px rgba(0, 229, 255, .5); color: #fff !important; }
.q-uploader { background: rgba(255, 255, 255, .04) !important; color: var(--txt) !important; border-radius: 12px; box-shadow: none; max-height: 56px; }
.q-uploader__list { display: none; }
.q-uploader__header { background: transparent !important; }
.q-uploader__subtitle { display: none; }

/* picture cards, the keep tray and the drop target */
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 12px; padding: 4px 16px 4px; }
.scroll { max-height: calc(100vh - 430px); min-height: 360px; overflow-y: auto; }
.tile { position: relative; border-radius: 14px; border: 1px solid rgba(255, 255, 255, .18); overflow: hidden; background: rgba(5, 7, 10, .6); cursor: grab; }
.tile:active { cursor: grabbing; }
.tile.kept { outline: 2px solid var(--cyan); box-shadow: 0 0 22px rgba(0, 229, 255, .55); }
.tile .stage { width: 100%; aspect-ratio: 1; display: grid; place-items: center; }
.tile .meta { padding: 6px 8px 8px; font-size: 11px; }
.tile .addbtn { position: absolute; top: 6px; right: 6px; z-index: 3; background: rgba(5, 7, 10, .75) !important; color: var(--cyan) !important; }
.drop { margin: 4px 16px; border: 1.5px dashed rgba(0, 229, 255, .45); border-radius: 14px; min-height: 220px; background: rgba(0, 229, 255, .04);
  padding: 10px; transition: all .15s ease; }
.drop.over { border-color: var(--cyan); background: rgba(0, 229, 255, .14); box-shadow: 0 0 26px rgba(0, 229, 255, .45) inset; }
.drop .hint { text-align: center; color: var(--mut); padding: 40px 10px; }
.kgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.kitem { position: relative; aspect-ratio: 1; border-radius: 10px; overflow: hidden; border: 1px solid rgba(255, 255, 255, .18); display: grid; place-items: center; }
.kitem .x { position: absolute; top: 2px; right: 2px; z-index: 3; background: rgba(5, 7, 10, .75) !important; color: var(--danger) !important; }
.bwfilter img { filter: grayscale(1) contrast(1.15); }
.q-log, .nicegui-log { background: rgba(0, 0, 0, .3) !important; border-radius: 12px; color: #9fb6cf; font-size: 11.5px; }
.q-linear-progress { border-radius: 9px; }
.q-linear-progress__track { background: rgba(255, 255, 255, .1); opacity: 1; }
.q-linear-progress__model { background: linear-gradient(90deg, #4ad8ff, #b9a7ff); box-shadow: 0 0 14px rgba(0, 229, 255, .7); }
.q-tooltip { background: rgba(12, 16, 24, .95); border: 1px solid rgba(255, 255, 255, .18); }
"""

# Slides the light streak with the cursor (or device tilt on phones and tablets).
HEAD_JS = """
<script>
(function () {
  var root = document.documentElement, raf = null, tx = 0, ty = 0;
  function apply() { raf = null;
    root.style.setProperty('--aniso-angle', (118 + tx * 14) + 'deg');
    root.style.setProperty('--aniso-shift', (tx * 10 + ty * 4) + '%'); }
  function queue(x, y) { tx = x; ty = y; if (!raf) raf = requestAnimationFrame(apply); }
  window.addEventListener('mousemove', function (e) { queue(e.clientX / innerWidth * 2 - 1, e.clientY / innerHeight * 2 - 1); });
  window.addEventListener('deviceorientation', function (e) {
    if (e.gamma == null) return;
    queue(Math.max(-1, Math.min(1, e.gamma / 30)), Math.max(-1, Math.min(1, (e.beta - 45) / 30))); });
  document.addEventListener('paste', function (e) {
    var items = e.clipboardData && e.clipboardData.items; if (!items) return;
    for (var i = 0; i < items.length; i++) { var it = items[i];
      if (it.kind === 'file' && it.type.indexOf('image/') === 0) { var f = it.getAsFile(), r = new FileReader();
        r.onload = function () { emitEvent('pasted_image', { data: r.result }); }; r.readAsDataURL(f); e.preventDefault(); } } });
})();
</script>
"""

BODY_HTML = '<div class="stars"></div>'
