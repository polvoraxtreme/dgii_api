from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from api.dgii_service import DgiiService

app = FastAPI(
    title="DGII API",
    description="Consulta de contribuyentes y validación de NCF en la DGII (República Dominicana)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/taxcontributors/{vat}")
def get_tax_contributor(vat: str):
    """
    Consulta un contribuyente por RNC o Cédula.
    - **vat**: RNC (9 dígitos) o Cédula (11 dígitos)
    """
    result = DgiiService.query_rnc(vat)

    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])

    return {
        "rnc":              result["rnc"],
        "razonSocial":      result["name"],
        "nombreComercial":  result["name_commercial"],
        "tipoContribuyente": result["contributor_type"],
        "actividadEconomica": result["commercial_type"],
        "estado":           result["status_raw"],
    }


@app.get("/api/citizens/{vat}")
def get_citizen(vat: str):
    """
    Consulta un ciudadano por Cédula.
    - **vat**: Cédula (11 dígitos)
    """
    result = DgiiService.query_rnc(vat)

    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])

    return {
        "rnc":    result["rnc"],
        "nombre": result["name"],
        "estado": result["status_raw"],
    }


@app.get("/api/ncf/{rnc_issuer}/{ncf}")
def validate_ncf(rnc_issuer: str, ncf: str,
                 rnc_consumer: str = "", security_code: str = ""):
    """
    Valida un NCF o e-NCF.
    - **rnc_issuer**: RNC del emisor
    - **ncf**: Número de comprobante fiscal (B... o E...)
    - **rnc_consumer**: RNC del comprador (opcional)
    - **security_code**: Código de seguridad (opcional)
    """
    result = DgiiService.validate_ncf(rnc_issuer, ncf, rnc_consumer, security_code)

    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["error"])

    return {
        "rncEmisor":      result["rnc_issuer"],
        "razonSocial":    result["contributor_name"],
        "ncf":            result["ncf"],
        "tipoNCF":        result["ncf_type"],
        "estado":         result["status"],
        "fechaVencimiento": result["due_date"].isoformat() if result["due_date"] else None,
        "esValido":       result["is_valid"],
    }


@app.get("/")
def root():
    return {"status": "ok", "docs": "/docs"}
