// Pisos de amostragem — espelho de backend/app/analytics.py.
// Ficam aqui só para desenhar a régua (Gauge); quem decide 'high'/'low' é
// sempre o backend, que manda `confidence` junto de cada recorte. Se mudar lá,
// mude aqui: a régua é a leitura visual do mesmo limiar.
export const MIN_MAP = 5;
export const MIN_AGENT = 3;
export const MIN_DUO = 4;
export const MIN_CLUTCH = 5;
export const MIN_MATCHES = 3;
export const MIN_SITE = 5;
export const MIN_WEAPON_ROUNDS = 15;
export const MIN_TREND = 10;
