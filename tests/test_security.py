import os, tempfile, unittest, io
from PIL import Image
_data = tempfile.TemporaryDirectory()
os.environ.update(VHD_DATA_DIR=_data.name, VHD_ENV='development', VHD_ADMIN_PASSWORD='test-only-password-123', VHD_SECRET_KEY='test-only-secret-not-for-deployment-123456', VHD_COOKIE_SECURE='0')
os.environ["DATABASE_URL"] = ""
os.environ.pop("VERCEL", None)
from app import app
class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.client=app.test_client()
        self.client.get('/')
        with self.client.session_transaction() as s: self.token=s['public_csrf']
        self.data=dict(fullName='Test Visitor',mobile='9876543210',visitorType='Guest',personToMeet='Reception',purpose='Test')
    def post(self, data, token=None):
        return self.client.post('/api/register',data=data,headers={'X-CSRF-Token': token if token is not None else self.token})
    def test_missing_selfie(self): self.assertEqual(self.post(self.data).status_code,400)
    def test_csrf(self): self.assertEqual(self.post(self.data,'bad').status_code,400)
    def test_invalid_image(self): self.assertEqual(self.post(dict(self.data,selfie=(io.BytesIO(b'not an image'),'fake.jpg'))).status_code,400)
    def test_valid_image(self):
        b=io.BytesIO(); Image.new('RGB',(30,30)).save(b,'PNG'); b.seek(0)
        self.assertEqual(self.post(dict(self.data,selfie=(b,'test.png'))).status_code,201)
    def test_admin_access(self):
        for p in ['/admin/export.csv','/admin/visitor/1/selfie','/admin/backup']:
            self.assertEqual(self.client.get(p).status_code,302)
    def test_headers(self): self.assertIn('blob:',self.client.get('/').headers['Content-Security-Policy'])
    def test_host(self): self.assertEqual(self.client.get('/',headers={'Host':'invalid.example'}).status_code,400)
if __name__=='__main__': unittest.main()
