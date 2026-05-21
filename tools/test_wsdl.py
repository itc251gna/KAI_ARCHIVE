from zeep import Client


# Diagnostic helper for inspecting the live ATLAS WSDL methods.
url = "http://www.idika.gov.gr/webservices/amka/epres_neu/Service.asmx?WSDL"

try:
    client = Client(url)
    print("--- AVAILABLE ATLAS METHODS (epres_neu) ---")
    client.wsdl.dump()
except Exception as e:
    print(f"Connection error: {e}")
