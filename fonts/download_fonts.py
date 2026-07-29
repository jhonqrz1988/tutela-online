import urllib.request, os
os.makedirs(os.path.dirname(__file__), exist_ok=True)
urls = [
    "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/DejaVuSans.ttf",
    "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/DejaVuSans-Bold.ttf",
    "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/DejaVuSans-Oblique.ttf",
]
for url in urls:
    name = url.split("/")[-1]
    path = os.path.join(os.path.dirname(__file__), name)
    print(f"Downloading {name}...")
    try:
        urllib.request.urlretrieve(url, path)
        print(f"  OK ({os.path.getsize(path)} bytes)")
    except Exception as e:
        print(f"  FAIL: {e}")