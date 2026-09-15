<script lang="ts">
  export let active = false;
  export let still = false;
</script>

<div class="media-illustration" class:active class:still aria-hidden="true">
  <div class="halo"></div>
  <svg viewBox="0 0 280 180" focusable="false">
    <g class="back-frame">
      <rect x="69" y="29" width="148" height="102" rx="14" />
      <path d="M85 43h10m12 0h10m12 0h10m12 0h10m12 0h10m12 0h6" />
    </g>
    <g class="picture-frame">
      <rect class="picture" x="50" y="45" width="164" height="110" rx="14" />
      <path class="mountain" d="M63 135l36-40 27 26 21-20 54 40H63z" />
      <path class="ridge" d="M63 135l36-40 27 26 21-20 54 40" />
      <g class="sun-group">
        <circle class="sun-glow" cx="172" cy="77" r="16" />
        <circle class="sun" cx="172" cy="77" r="10" />
      </g>
      <path class="scan" d="M64 59v82" />
      <rect class="sheen" x="50" y="45" width="164" height="110" rx="14" />
    </g>
    <g class="play">
      <circle class="play-ring" cx="213" cy="134" r="24" />
      <circle cx="213" cy="134" r="24" />
      <path class="play-mark" d="M208 124v20l15-10z" />
    </g>
    <g class="pixels">
      <rect class="pixel-a" x="35" y="74" width="6" height="6" rx="2" />
      <rect class="pixel-b" x="231" y="63" width="9" height="9" rx="3" />
      <rect class="pixel-c" x="91" y="166" width="5" height="5" rx="2" />
    </g>
    <path class="spark" d="M231 25v12m-6-6h12" />
  </svg>
</div>

<style>
  .media-illustration {
    position: relative;
    width: min(280px, 65vw);
    margin: 0 auto 18px;
  }
  svg {
    position: relative;
    display: block;
    width: 100%;
    overflow: visible;
  }
  .halo {
    position: absolute;
    inset: 10% 2%;
    border-radius: 50%;
    background: radial-gradient(ellipse, #467eea2e, transparent 68%);
    animation: halo 7s ease-in-out infinite;
  }
  .back-frame {
    transform: rotate(8deg);
    transform-origin: 140px 80px;
    animation: back-float 7s ease-in-out infinite;
  }
  .back-frame rect {
    fill: #25324c;
    stroke: #53709d;
  }
  .back-frame path {
    stroke: #88a8d5;
    stroke-width: 3;
    stroke-linecap: round;
  }
  .picture-frame {
    animation: float 6s ease-in-out infinite;
  }
  .picture {
    fill: #202938;
    stroke: #7596c9;
    stroke-width: 1.5;
  }
  .mountain {
    fill: #36557c;
  }
  .ridge {
    fill: none;
    stroke: #96bce8;
    stroke-width: 2;
    stroke-linejoin: round;
  }
  .sun {
    fill: #a0c8fa;
  }
  .sun-glow {
    fill: #a0c8fa;
    opacity: 0.18;
    transform-origin: 172px 77px;
    animation: glow 4s ease-in-out infinite;
  }
  .sheen {
    fill: white;
    opacity: 0;
    transform-origin: 132px 100px;
    animation: sheen 9s ease-in-out infinite;
  }
  .play {
    transform-origin: 213px 134px;
    animation: play-float 5s ease-in-out infinite;
  }
  .play circle:not(.play-ring) {
    fill: #497be0;
    stroke: #a7c8ff;
    stroke-width: 1.5;
  }
  .play-ring {
    fill: none;
    stroke: #6f98f0;
    stroke-width: 1.5;
    opacity: 0;
    transform-origin: 213px 134px;
    animation: ring 5s ease-out infinite;
  }
  .play-mark {
    fill: white;
  }
  .pixels rect {
    fill: #739bea;
  }
  .pixel-a {
    animation:
      drift 6.5s ease-in-out infinite,
      breathe 5s ease-in-out infinite;
  }
  .pixel-b {
    animation:
      drift 8s ease-in-out -2s infinite,
      breathe 6s ease-in-out -1s infinite;
  }
  .pixel-c {
    animation:
      drift 7s ease-in-out -4s infinite,
      breathe 5.5s ease-in-out -3s infinite;
  }
  .spark {
    stroke: #9fbfec;
    stroke-width: 2;
    stroke-linecap: round;
    transform-origin: 231px 31px;
    animation: twinkle 4.5s ease-in-out infinite;
  }
  .scan {
    opacity: 0;
    stroke: #b2d6ff;
    stroke-width: 2;
    animation: scan 8s ease-in-out infinite;
  }
  .active .scan {
    animation: scan 2.4s ease-in-out infinite;
  }
  .active .pixels rect {
    animation-duration: 1.6s;
  }
  .still * {
    animation: none !important;
  }
  @keyframes float {
    50% {
      transform: translateY(-4px);
    }
  }
  @keyframes back-float {
    50% {
      transform: rotate(9.5deg) translateY(3px);
    }
  }
  @keyframes play-float {
    50% {
      transform: translateY(3px) scale(1.03);
    }
  }
  @keyframes ring {
    0%,
    55% {
      opacity: 0;
      transform: scale(1);
    }
    65% {
      opacity: 0.7;
    }
    100% {
      opacity: 0;
      transform: scale(1.55);
    }
  }
  @keyframes glow {
    50% {
      opacity: 0.42;
      transform: scale(1.25);
    }
  }
  @keyframes halo {
    50% {
      opacity: 0.55;
      transform: scale(1.06);
    }
  }
  @keyframes breathe {
    50% {
      opacity: 0.35;
    }
  }
  @keyframes drift {
    50% {
      transform: translateY(-5px);
    }
  }
  @keyframes twinkle {
    0%,
    100% {
      opacity: 0.45;
      transform: rotate(0deg) scale(0.85);
    }
    50% {
      opacity: 1;
      transform: rotate(90deg) scale(1.1);
    }
  }
  @keyframes sheen {
    0%,
    70% {
      opacity: 0;
      transform: translateX(-90px) skewX(-18deg);
    }
    80% {
      opacity: 0.08;
    }
    100% {
      opacity: 0;
      transform: translateX(120px) skewX(-18deg);
    }
  }
  @keyframes scan {
    0%,
    62% {
      opacity: 0;
      transform: translateX(0);
    }
    70%,
    88% {
      opacity: 0.8;
    }
    100% {
      opacity: 0;
      transform: translateX(135px);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    * {
      animation: none !important;
    }
    .active .scan {
      opacity: 0.5;
      transform: translateX(65px);
    }
  }
</style>
