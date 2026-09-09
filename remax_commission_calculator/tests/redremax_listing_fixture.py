"""Sanitized RedREMAX listing contract. No real clients, documents, or tokens."""

SANITIZED_LISTING = {
    "id": "AR.TEST.27.251.203",
    "associate": "AR.TEST.27.251",
    "associateQrid": "QR-TEST-251",
    "office": "AR.TEST.27",
    "network": "AR",
    "node": "TEST",
    "qrid": "QR-TEST-203",
    "mlsid": "MLS-TEST-203",
    "title": "Propiedad de prueba",
    "description": "Descripción de prueba",
    "type": "sale",
    "propertyType": "Terrenos y Lotes",
    "location": [-58.79, -34.43],
    "address": {
        "displayAddress": "Av. Ensayo 123",
        "streetName": "Av. Ensayo",
        "streetNumber": "123",
        "apartment": None,
        "floor": None,
        "city": "Pilar",
        "county": "Pilar",
        "neighborhood": "Test",
        "privatecommunity": None,
        "subregion": "Norte",
        "region": "Buenos Aires",
        "state": "Buenos Aires",
        "country": "AR",
        "postalCode": "1629",
    },
    "dimensions": {
        "land": 3537,
        "totalBuilt": 0,
        "covered": None,
        "uncovered": None,
        "semicovered": None,
    },
    "price": {
        "value": 8000000,
        "currency": "USD",
        "exposure": "private",
        "type": "asking",
    },
    "priceHistory": [
        {
            "price": 8000000,
            "date": "2026-08-01",
            "currency": "USD",
            "usd": 8000000,
            "tc": 1,
            "local": 8000000,
            "localCurrency": "USD",
        }
    ],
    "photos": [
        {
            "cdn": "https://redremax-images.s3.amazonaws.com/test/photo.jpg",
            "primary": True,
        },
        {
            "cdn": "https://redremax-images.s3.amazonaws.com/test/photo-2.jpg",
            "primary": False,
        },
    ],
    "blueprints": [
        {
            "cdn": "https://redremax-images.s3.amazonaws.com/test/plan.jpg?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=3600&X-Amz-Signature=abc"
        }
    ],
    "documents": [
        {"name": "escritura.pdf", "url": "https://example.invalid/escritura.pdf"}
    ],
    "clients": [{"name": "should-not-store"}],
    "clientsData": [{"email": "hidden@example.invalid"}],
    "privateNotes": "not for JRH",
    "features": [35, 41],
    "bedrooms": 0,
    "bathrooms": 0,
    "toiletrooms": 0,
    "totalRooms": 0,
    "rooms": [],
    "parkingSpaces": 0,
    "status": "active",
    "statusChanges": "approved",
    "associateStatus": "active",
    "updatedAt": "2026-09-07 16:39:40",
    "createdAt": "2026-08-01 10:00:00",
}
