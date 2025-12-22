# app/services/evidence_service.py
from __future__ import annotations

from typing import Any

from app.models import Nota


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def build_evidence_groups(nota: Nota) -> list[dict]:
    """
    Construye grupos de evidencia por material para visualizacion en templates.
    """
    groups: list[dict] = []
    materiales = sorted(
        nota.materiales,
        key=lambda m: (m.orden is None, m.orden or 0, m.id),
    )
    for idx, nm in enumerate(materiales, start=1):
        material_name = nm.material.nombre if nm.material else f"Material {nm.material_id}"
        unit = nm.material.unidad_medida if nm.material else "kg"
        sub_list: list[dict] = []
        subpesajes = list(nm.subpesajes or [])
        for sidx, sp in enumerate(subpesajes, start=1):
            peso = _as_float(sp.peso_kg)
            desc = _as_float(sp.descuento_kg)
            sub_list.append(
                {
                    "id": sp.id,
                    "order": sidx,
                    "peso_kg": peso,
                    "descuento_kg": desc,
                    "neto_kg": max(peso - desc, 0.0),
                    "foto_url": sp.foto_url,
                    "created_at": sp.created_at,
                }
            )
        groups.append(
            {
                "material_id": nm.material_id,
                "material_name": material_name,
                "unit": unit,
                "order": nm.orden if nm.orden is not None else idx,
                "subpesajes": sub_list,
            }
        )
    return groups
