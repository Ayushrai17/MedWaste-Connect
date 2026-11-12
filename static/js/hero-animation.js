/**
 * MedWaste Connect — Cinematic Vehicle Emergence & Ultra-Smooth Driveway Transit (STEP 3.3)
 * Full-Bleed 2.5D Architectural Scene & Mathematically Continuous Motion
 */

(function () {
  'use strict';

  // Fallback handler if GSAP fails to load
  function handleGsapFallback() {
    console.warn('[MedWaste Step 3.3] GSAP not detected. Activating static parked fallback.');
    const v = document.querySelector('.hero-layer-van');
    const s = document.querySelector('.hero-layer-shadow');
    if (v) {
      v.style.transform = 'none';
      v.style.zIndex = '50';
    }
    if (s) {
      s.style.transform = 'none';
      s.style.zIndex = '40';
      s.style.opacity = '0.85';
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initStep33Emergence);
  } else {
    initStep33Emergence();
  }

  function initStep33Emergence() {
    console.log('================================================================');
    console.log('[MedWaste Step 3.3] Initializing Full-Bleed 2.5D Ultra-Smooth Scene');
    console.log('================================================================');

    if (typeof gsap === 'undefined') {
      handleGsapFallback();
      return;
    }
    if (typeof MotionPathPlugin !== 'undefined') {
      gsap.registerPlugin(MotionPathPlugin);
    }
    console.log('[MedWaste Step 3.3] GSAP version detected:', gsap.version);

    const stage = document.getElementById('heroSceneStage');
    const hospital = document.querySelector('.hero-layer-hospital');
    const driveway = document.querySelector('.hero-layer-driveway');
    const wing = document.querySelector('.hero-layer-wing');
    const bins = document.querySelector('.hero-layer-bins');
    const van = document.querySelector('.hero-layer-van');
    const shadow = document.querySelector('.hero-layer-shadow');
    const replayBtn = document.getElementById('btnReplayHero');

    if (!stage || !hospital || !driveway || !wing || !bins || !van || !shadow) {
      console.error('[MedWaste Step 3.3] ERROR: Required scene layers not found!');
      return;
    }

    // STRICT REQUIREMENT: Hospital, driveway, and bins are stationary
    gsap.set(hospital, { opacity: 1, x: 0, y: 0, scale: 1.0, zIndex: 10 });
    gsap.set(driveway, { opacity: 1, x: 0, y: 0, zIndex: 20 });
    gsap.set(bins, { opacity: 1, x: 0, y: 0, scale: 1.0, zIndex: 60 });
    // Foreground physical wing cutout at zIndex 26 (solid alpha = 255 covering x >= 1260)
    gsap.set(wing, { display: 'block', opacity: 1, x: 0, y: 0, zIndex: 26 });

    // Pivot points
    gsap.set(van, { transformOrigin: '35% 85%' });
    gsap.set(shadow, { transformOrigin: 'center bottom' });

    // Initial state matching the CSS definition (Zero Flash of Parked State)
    // Van starts in hospital courtyard behind the right wing (xPercent: 140, yPercent: -24)
    // Explicitly reset pixel x and y to 0 to prevent compounding with CSS translate
    const INITIAL_VAN = {
      x: 0,
      y: 0,
      xPercent: 140,
      yPercent: -24,
      scale: 0.38,
      rotation: -5.0,
      opacity: 1,
      zIndex: 22
    };

    const INITIAL_SHADOW = {
      x: 0,
      y: 0,
      xPercent: 140,
      yPercent: -24,
      scaleX: 0.38,
      scaleY: 0.28,
      rotation: -5.0,
      opacity: 0.35,
      zIndex: 21
    };

    gsap.set(van, INITIAL_VAN);
    gsap.set(shadow, INITIAL_SHADOW);

    // Master Timeline (Total Duration: 5.0 seconds)
    const tl = gsap.timeline({
      paused: false,
      onStart: function () {
        console.log('[MedWaste Step 3.4] >>> Timeline STARTED. Checkpoint 0s: Van 100% hidden in courtyard behind right wing.');
      },
      onComplete: function () {
        console.log('[MedWaste Step 3.4] >>> Timeline COMPLETED. Checkpoint 5s: Van settled completely stationary.');
        console.log(`[MedWaste Step 3.4]     Final van state: xPercent=${gsap.getProperty(van, 'xPercent')}, scale=${gsap.getProperty(van, 'scale')}`);
      }
    });

    // ------------------------------------------------------------------------
    // CHECKPOINT 0s: 0.0s – 0.6s: INITIAL STATE (Van 100% hidden behind right wing)
    // ------------------------------------------------------------------------
    tl.set(van, INITIAL_VAN, 0);
    tl.set(shadow, INITIAL_SHADOW, 0);

    // Timeline Progress Loggers at exact seconds
    tl.call(() => console.log('[MedWaste Step 3.4] Checkpoint 1s: Front grille & headlights emerging past building corner...'), null, 1.0);
    tl.call(() => console.log('[MedWaste Step 3.4] Checkpoint 2s: Mid-emergence — biohazard emblem & cargo section emerge...'), null, 2.0);
    tl.call(() => console.log('[MedWaste Step 3.4] Checkpoint 3s: Cleared building — traveling continuously across driveway plaza...'), null, 3.0);
    tl.call(() => console.log('[MedWaste Step 3.4] Checkpoint 4s: Approaching parking bay — smooth natural deceleration...'), null, 4.0);
    tl.call(() => console.log('[MedWaste Step 3.4] Checkpoint 5s: Finished — van completely stationary at rest.'), null, 5.0);

    // ------------------------------------------------------------------------
    // ULTRA-SMOOTH CONTINUOUS TRAJECTORY: 0.6s – 4.2s (Duration: 3.6s)
    // Continuous Catmull-Rom spline curve with single unified power2.inOut ease.
    // Zero keyframe seams, zero velocity drops, zero micro-stutters.
    // Starts inside courtyard (140%, -24%) and smoothly travels to parked (0%, 0%)
    // ------------------------------------------------------------------------
    tl.to(van, {
      duration: 3.6,
      motionPath: {
        path: [
          { xPercent: 140, yPercent: -24 },
          { xPercent: 105, yPercent: -17 },
          { xPercent: 65,  yPercent: -10 },
          { xPercent: 18,  yPercent: -2 },
          { xPercent: 0,   yPercent: 0 }
        ],
        curviness: 1.2
      },
      scale: 1.0,
      rotation: 0,
      ease: 'power2.inOut'
    }, 0.6);

    // Synchronized Ground Shadow along identical motion path
    tl.to(shadow, {
      duration: 3.6,
      motionPath: {
        path: [
          { xPercent: 140, yPercent: -24 },
          { xPercent: 105, yPercent: -17 },
          { xPercent: 65,  yPercent: -10 },
          { xPercent: 18,  yPercent: -2 },
          { xPercent: 0,   yPercent: 0 }
        ],
        curviness: 1.2
      },
      scaleX: 1.0,
      scaleY: 1.0,
      opacity: 0.85,
      rotation: 0,
      ease: 'power2.inOut'
    }, 0.6);

    // Elevate zIndex to mid-ground at 2.5s as vehicle rear completely clears building wing pillar (zIndex: 26)
    // Van travels BEHIND foreground waste bins (zIndex: 60), ensuring bins naturally occlude the vehicle
    tl.set(van, { zIndex: 35 }, 2.5);
    tl.set(shadow, { zIndex: 28 }, 2.5);

    // ------------------------------------------------------------------------
    // PARKING DECELERATION DIP & ELASTIC SETTLE: 4.2s – 5.0s
    // ------------------------------------------------------------------------
    // Front suspension compresses under braking (4.2s – 4.45s)
    tl.to(van, {
      y: 3.5,
      rotation: -0.6,
      duration: 0.25,
      ease: 'power1.in'
    }, 4.2)
    .to(shadow, {
      scaleX: 1.03,
      opacity: 0.95,
      duration: 0.25,
      ease: 'power1.in'
    }, 4.2)
    // Gentle elastic suspension rebound to neutral rest (4.45s – 5.0s)
    .to(van, {
      y: 0,
      rotation: 0,
      duration: 0.55,
      ease: 'elastic.out(1.15, 0.45)'
    }, 4.45)
    .to(shadow, {
      scaleX: 1.0,
      opacity: 0.85,
      duration: 0.55,
      ease: 'power2.out'
    }, 4.45);

    // Expose timeline globally
    window.__MEDWASTE_STEP36_TL__ = tl;
    window.__MEDWASTE_STEP34_TL__ = tl;
    window.__MEDWASTE_STEP33_TL__ = tl;
    window.__MEDWASTE_STEP32_TL__ = tl;

    // Replay handlers
    if (replayBtn) {
      replayBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        console.log('[MedWaste Step 3.4] Replay button clicked.');
        tl.restart();
      });
    }

    stage.style.cursor = 'pointer';
    stage.addEventListener('click', function () {
      console.log('[MedWaste Step 3.4] Stage clicked: replaying emergence & parking.');
      tl.restart();
    });
  }
})();
