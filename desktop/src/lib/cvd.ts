type CvdView = 'normal' | 'protan' | 'deutan' | 'tritan';

type Matrix3 = readonly [
  readonly [number, number, number],
  readonly [number, number, number],
  readonly [number, number, number],
];

// Machado, Oliveira & Fernandes (2009), severity 1.0, applied to linear RGB.
const CVD_MATRICES: Record<CvdView, Matrix3> = {
  normal: [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ],
  protan: [
    [0.152286, 1.052583, -0.204868],
    [0.114503, 0.786281, 0.099216],
    [-0.003882, -0.048116, 1.051998],
  ],
  deutan: [
    [0.367322, 0.860646, -0.227968],
    [0.280085, 0.672501, 0.047413],
    [-0.01182, 0.04294, 0.968881],
  ],
  tritan: [
    [1.255528, -0.076749, -0.178779],
    [-0.078411, 0.930809, 0.147602],
    [0.004733, 0.691367, 0.3039],
  ],
};

function parseHex(hex: string): [number, number, number] {
  let body = hex.trim().replace(/^#/, '');
  if (body.length === 3) {
    body = [...body].map((c) => c + c).join('');
  }
  return [
    parseInt(body.slice(0, 2), 16) / 255,
    parseInt(body.slice(2, 4), 16) / 255,
    parseInt(body.slice(4, 6), 16) / 255,
  ];
}

function srgbToLinear(c: number): number {
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function linearTo8BitLinear(c: number): number {
  const clamped = Math.min(1, Math.max(0, c));
  const encoded = clamped <= 0.0031308 ? 12.92 * clamped : 1.055 * clamped ** (1 / 2.4) - 0.055;
  const quantized = Math.round(encoded * 255) / 255;
  return srgbToLinear(quantized);
}

function simulateLinear(
  rgb: readonly [number, number, number],
  view: CvdView,
): [number, number, number] {
  const m = CVD_MATRICES[view];
  return [
    linearTo8BitLinear(m[0][0] * rgb[0] + m[0][1] * rgb[1] + m[0][2] * rgb[2]),
    linearTo8BitLinear(m[1][0] * rgb[0] + m[1][1] * rgb[1] + m[1][2] * rgb[2]),
    linearTo8BitLinear(m[2][0] * rgb[0] + m[2][1] * rgb[1] + m[2][2] * rgb[2]),
  ];
}

function linearToOklab([r, g, b]: readonly [number, number, number]): [number, number, number] {
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

export function smallestCvdDistance(hexColors: readonly string[]): number {
  const linearColors = hexColors.map((hex) => {
    const [r, g, b] = parseHex(hex);
    return [srgbToLinear(r), srgbToLinear(g), srgbToLinear(b)] as const;
  });
  let minDistance = Infinity;
  for (const view of ['normal', 'protan', 'deutan', 'tritan'] as const) {
    const labs = linearColors.map((rgb) => linearToOklab(simulateLinear(rgb, view)));
    for (let i = 0; i < labs.length; i++) {
      for (let j = i + 1; j < labs.length; j++) {
        const d =
          Math.hypot(labs[i][0] - labs[j][0], labs[i][1] - labs[j][1], labs[i][2] - labs[j][2]) *
          100;
        if (d < minDistance) minDistance = d;
      }
    }
  }
  return minDistance;
}
