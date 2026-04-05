# app/models/__init__.py
from .user import User, UserRole, UserStatus
from .branch import Sucursal, SucursalStatus
from .material import Material
from .pricing import TablaPrecio, TipoOperacion, TipoCliente, PriceChangeLog
from .account import Cuenta, CuentaScrap360, CuentaScrap360Movimiento, cuentas_scrap360_sucursales
from .partner import Proveedor, Cliente, ProveedorPlaca, ClientePlaca, AjusteSaldoPartner
from .comision import (
    Comisionario,
    ComisionarioNota,
    ComisionarioNotaMaterial,
    ComisionarioNotaEstado,
    ComisionarioPago,
)
from .conversion import ConversionMaterial, ConversionMaterialReversion
from .cash import (
    CorteCaja,
    CorteCajaEstado,
    CorteCajaGasto,
    CorteCajaMovimiento,
    CorteCajaMovimientoTipo,
    CorteCajaDenominacion,
)
from .note import (
    Nota,
    NotaEstado,
    NotaMaterial,
    Subpesaje,
    NotaOriginal,
    NotaEvidenciaExtra,
    NotaPago,
    NotaAjusteSaldo,
    NotaDevolucionParcial,
    NotaDevolucionParcialLinea,
    NotaDevolucionParcialAplicacion,
    NotaDevolucionTotal,
)
from .inventory import (
    Inventario,
    InventarioMovimiento,
    MovimientoContable,
    InventarioAjusteManual,
    InventarioValorPrecio,
)

__all__ = [
    "User",
    "UserRole",
    "UserStatus",
    "Sucursal",
    "SucursalStatus",
    "Material",
    "TablaPrecio",
    "TipoOperacion",
    "TipoCliente",
    "PriceChangeLog",
    "Cuenta",
    "CuentaScrap360",
    "CuentaScrap360Movimiento",
    "cuentas_scrap360_sucursales",
    "Proveedor",
    "Cliente",
    "ProveedorPlaca",
    "ClientePlaca",
    "AjusteSaldoPartner",
    "Comisionario",
    "ComisionarioNota",
    "ComisionarioNotaMaterial",
    "ComisionarioNotaEstado",
    "ComisionarioPago",
    "ConversionMaterial",
    "ConversionMaterialReversion",
    "CorteCaja",
    "CorteCajaEstado",
    "CorteCajaGasto",
    "CorteCajaMovimiento",
    "CorteCajaMovimientoTipo",
    "CorteCajaDenominacion",
    "Nota",
    "NotaEstado",
    "NotaMaterial",
    "Subpesaje",
    "NotaOriginal",
    "NotaEvidenciaExtra",
    "NotaPago",
    "NotaAjusteSaldo",
    "NotaDevolucionParcial",
    "NotaDevolucionParcialLinea",
    "NotaDevolucionParcialAplicacion",
    "NotaDevolucionTotal",
    "Inventario",
    "InventarioMovimiento",
    "MovimientoContable",
    "InventarioAjusteManual",
    "InventarioValorPrecio",
]

