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
      <circle class="sun" cx="172" cy="77" r="10" />
      <path class="scan" d="M64 59v82" />
    </g>
    <g class="play">
      <circle cx="213" cy="134" r="24" />
      <path class="play-mark" d="M208 124v20l15-10z" />
    </g>
    <g class="pixels"
      ><rect x="35" y="74" width="6" height="6" rx="2" /><rect
        x="231"
        y="63"
        width="9"
        height="9"
        rx="3"
      /><rect x="91" y="166" width="5" height="5" rx="2" /></g
    >
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
    inset: 12% 4%;
    border-radius: 50%;
    background: radial-gradient(ellipse, #467eea24, transparent 70%);
  }
  .back-frame {
    transform: rotate(8deg);
    transform-origin: 140px 80px;
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
  .play circle {
    fill: #497be0;
    stroke: #a7c8ff;
    stroke-width: 1.5;
  }
  .play-mark {
    fill: white;
  }
  .pixels {
    fill: #739bea;
    animation: breathe 5s ease-in-out infinite;
  }
  .spark {
    stroke: #9fbfec;
    stroke-width: 2;
    stroke-linecap: round;
  }
  .picture-frame {
    animation: float 6s ease-in-out infinite;
  }
  .scan {
    opacity: 0;
    stroke: #b2d6ff;
    stroke-width: 2;
  }
  .active .scan {
    animation: scan 2.4s ease-in-out infinite;
  }
  .active .pixels {
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
  @keyframes breathe {
    50% {
      opacity: 0.35;
    }
  }
  @keyframes scan {
    0% {
      opacity: 0;
      transform: translateX(0);
    }
    20%,
    75% {
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
