from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from api.dgii_service import DgiiService

app = FastAPI(
    title="DGII API",
    description="Consulta de contribuyentes y validación de NCF en la DGII (República Dominicana)",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/taxcontributors/{rnc}")
def get_tax_contributor(rnc: str):
    result = DgiiService.query_rnc(rnc)

    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])

    return {
        "rnc":                result["rnc"],
        "razonSocial":        result["name"],
        "nombreComercial":    result["name_commercial"],
        "actividadEconomica": result["commercial_type"],
        "estado":             result["status_raw"],
        "esContribuyente":    True,
    }


@app.get("/api/citizens/{identity_number}")
def get_citizen(identity_number: str):
    result = DgiiService.query_citizen(identity_number)

    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])

    return {
        "numeroIdentidad":  result["rnc"],
        "nombre":           result["name"],
        "estado":           result["status_raw"],
        "esContribuyente":  True,
    }


@app.post("/api/taxreceiptsnumbers")
def validate_tax_receipt_number(
    rncIssuer: str = Form(...),
    trn: str = Form(...),
    rncConsumer: Optional[str] = Form(""),
    securityCode: Optional[str] = Form(""),
):
    result = DgiiService.validate_ncf(
        rncIssuer, trn, rncConsumer or "", securityCode or ""
    )

    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["error"])

    return {
        "isValid":             result["is_valid"],
        "dueDate":             result["due_date"].strftime("%Y-%m-%dT%H:%M:%S") if result["due_date"] else None,
        "rnc":                 result["rnc_issuer"],
        "ncf":                 result["ncf"],
        "taxContibutorName":   result["contributor_name"],
    }


@app.get("/")
def root():
    return {"status": "ok", "docs": "/docs"}
