from streamlit.testing.v1 import AppTest

def test_display():
    at = AppTest.from_file("PGapp.py").run()
    