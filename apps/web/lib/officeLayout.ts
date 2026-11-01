// Layout puro do escritório isométrico (testável sem WebGL).

// Eixo horizontal da TELA na vista isométrica (câmera em +x,+y,+z): direção (1, 0, -1) no mundo.
export function slotOffset(i: number, n: number, spacing = 2.3): [number, number] {
  const k = (i - (n - 1) / 2) * spacing / Math.SQRT2;
  return [k, -k];
}

