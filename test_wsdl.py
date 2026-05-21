from zeep import Client

# Το δεύτερο URL από το αρχείο ρυθμίσεών σου
url = 'http://www.idika.gov.gr/webservices/amka/epres_neu/Service.asmx?WSDL'

try:
    client = Client(url)
    print("--- ΔΙΑΘΕΣΙΜΕΣ ΜΕΘΟΔΟΙ ΑΤΛΑΣ (epres_neu) ---")
    client.wsdl.dump()
except Exception as e:
    print(f"Σφάλμα σύνδεσης: {e}")