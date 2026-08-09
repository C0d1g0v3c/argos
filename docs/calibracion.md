# Experimento de calibración

Medir la brecha empírica entre el fill del líder y el propio, para calibrar el
modelo de fricción de `paper/`.

**Lo que este experimento NO mide:** si copy trading es rentable. Con este
capital y este número de operaciones, cualquier resultado de P&L es ruido.

## Diseño

| Parámetro | Valor |
|---|---|
| Capital | $500 MXN |
| Líder | 1, elegido del cohorte congelado con score de vigilancia bajo |
| Tamaño por operación | Mínimo permitido por la plataforma |
| N objetivo | 15–20 fills |
| Instrumento | Preferentemente spot |

## Criterios de paro

Definidos antes de empezar. Lo primero que ocurra:

- 20 fills registrados
- 30 días transcurridos
- Pérdida acumulada de $200 MXN (40% del capital)

Al cumplirse cualquiera: se cierra todo, se exportan los datos, se apaga.

## Registro

Cada fill se escribe en `real_fill`; la vista `fill_slippage` deriva latencia
y slippage adverso. Cada evento gravable se registra en `tax_event` al momento.

## Salida esperada

Un número: **el costo en puntos base por segundo de latencia de copia**,
medido en cuenta propia, en esta plataforma.
