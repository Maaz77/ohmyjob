"""Tests for the word lists (lexicon.py)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import lexicon as X  # noqa: E402
import aa_lib as L  # noqa: E402


class TestNorm(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(X.norm("  Envoyé MA candidature "), "envoye ma candidature")
        self.assertEqual(X.norm("Abschließen"), "abschliessen")
        self.assertEqual(X.norm("Başvur"), "basvur")
        self.assertEqual(X.norm("İptal"), "iptal")
        self.assertEqual(X.norm("ΥΠΟΒΟΛΉ"), X.norm("υποβολη"))
        self.assertEqual(X.norm("إرسال"), X.norm("ارسال"))
        self.assertEqual(X.norm("ثبت‌نام"), "ثبت نام")
        self.assertEqual(X.norm("応募する"), "応募する")          # Japanese is kept as it is
        self.assertEqual(X.norm("l’annuncio"), "l'annuncio")


class TestButtons(unittest.TestCase):
    def test_final_in_many_languages(self):
        finals = ["Submit application", "Invia candidatura", "Envoyer ma candidature", "Bewerbung absenden",
                  "Enviar solicitud", "Submeter candidatura", "Verzenden", "Wyślij aplikację", "Skicka ansökan",
                  "Lähetä hakemus", "Odeslat", "Trimite", "Küldés", "Υποβολή αίτησης", "Başvuruyu gönder",
                  "Отправить", "Надіслати", "إرسال الطلب", "שלח", "ارسال درخواست", "提交申请", "送信する", "제출",
                  "ส่งใบสมัคร", "Gửi hồ sơ", "Kirim lamaran", "Done", "Finish", "Fertig", "Gotowe", "完了"]
        for name in finals:
            self.assertTrue(X.FINAL.find(name), name)

    def test_not_final(self):
        for name in ["Next", "Continue to next step", "Avanti", "Weiter", "Suivant", "Siguiente", "Próximo",
                     "Volgende", "Dalej", "Далее", "Review your application", "Save", "Upload resume", "次へ",
                     "다음", "التالي", "Easy Apply", "Rivedi", "Sign in with Google"]:
            self.assertIsNone(X.FINAL.find(name), name)

    def test_apply_consent_cookie_signup_delete(self):
        for name in ["Easy Apply", "Reapply", "Candidati ora", "Postuler", "Jetzt bewerben", "Solicitar", "Solliciteer",
                     "Aplikuj", "Ansök", "Hae nyt", "Başvur", "Откликнуться", "応募", "申请", "지원하기", "Lamar"]:
            self.assertTrue(X.APPLY.find(name), name)
        for name in ["I agree", "Accetto", "J'accepte", "Ich stimme zu", "Acepto", "Concordo", "Akkoord", "Godkänn",
                     "Hyväksyn", "Souhlasím", "Zgadzam się", "Elfogadom", "Принять", "同意", "동의", "Kabul ediyorum"]:
            self.assertTrue(X.CONSENT.find(name), name)
        for text in ["Accept all cookies", "Accetta tutti i cookie", "Alle Cookies akzeptieren", "Kakor", "Evästeasetukset",
                     "Ciasteczka", "Çerezleri kabul et", "クッキー", "쿠키 허용"]:
            self.assertTrue(X.COOKIE.find(text), text)
        for name in ["Allow", "Consenti", "Autoriser", "Zulassen", "Permitir", "Toestaan", "Разрешить", "允许", "許可", "허용"]:
            self.assertTrue(X.PERMISSION.find(name), name)
        for name in ["Create an account", "Crea un account", "Créer un compte", "Konto erstellen", "Registrarse",
                     "Criar conta", "Account aanmaken", "Zarejestruj się", "Регистрация", "注册", "新規登録", "회원가입"]:
            self.assertTrue(X.SIGNUP.find(name), name)
        for name in ["Delete", "Elimina il mio profilo", "Supprimer", "Löschen", "Eliminar", "Usuń", "Удалить", "删除", "削除"]:
            self.assertTrue(X.DELETE.find(name), name)
        for name in ["Withdraw", "Zurückziehen", "Intrekken", "撤回", "辞退", "Visszavonás"]:
            self.assertTrue(X.WITHDRAW.find(name), name)
            self.assertFalse(X.REMOVE.find(name), name)
        for name in ["Delete", "Remove", "Rimuovi", "Quitar", "Remover", "Entfernen", "Löschen", "Elimina", "Törölni"]:
            self.assertTrue(X.REMOVE.find(name), name)
            self.assertFalse(X.WITHDRAW.find(name), name)
            self.assertNotIn(name, X.SAFE)  # a delete word is never allowed on its name (issue 45)
        for name in ["Continue with Google", "Accedi con Google", "Mit Google anmelden", "Googleでログイン"]:
            self.assertTrue(X.GOOGLE.find(name), name)

    def test_safe_needs_the_whole_name(self):
        self.assertIn("Next", X.SAFE)
        self.assertIn("  Avanti › ", X.SAFE)
        self.assertIn("Continue to next step", X.SAFE)
        self.assertNotIn("Next and submit", X.SAFE)
        self.assertNotIn("OK", X.SAFE)

    def test_the_german_step_words_are_all_three(self):
        # "fortsetzen" was missing beside "weiter" and "fortfahren", so "Bewerbung fortsetzen" (issue 23) could not
        # stand the apply rule down. A step word inside a longer name is found with `find`, not `in`.
        for name in ("Weiter", "Fortfahren", "Fortsetzen"):
            self.assertIn(name, X.SAFE)
        self.assertEqual(X.SAFE.find("Bewerbung fortsetzen"), "fortsetzen")

    def test_submitted_messages(self):
        for text in ["Your application was sent to Acme", "Grazie per la tua candidatura!", "Vielen Dank für Ihre Bewerbung",
                     "Merci pour votre candidature", "ご応募ありがとうございます", "申请已提交"]:
            self.assertTrue(X.SUBMITTED.find(text), text)
        self.assertIsNone(X.SUBMITTED.find("Review your application"))


class TestOAuthUrl(unittest.TestCase):
    def test_a_real_provider_page_matches(self):
        for url in ("https://provider.example.com/oauth/authorize?x=1",
                    "https://appleid.apple.com/auth/authorize?client_id=1",
                    "https://login.microsoftonline.com/common/oauth2/authorize?x=1",
                    "https://oauth.vk.com/authorize",
                    "https://www.example.com/sso/oauth2"):
            self.assertTrue(L.OAUTH_URL_RE.search(url), url)

    def test_a_job_slug_is_no_sign_in_page(self):
        for url in ("https://jobs.example.com/careers/authorize-solutions-engineer/123",
                    "https://boards.example.com/jobs/oauth-integration-engineer",
                    "https://jobs.example.com/roles/authorized-signatory-manager"):
            self.assertIsNone(L.OAUTH_URL_RE.search(url), url)


if __name__ == "__main__":
    unittest.main()
