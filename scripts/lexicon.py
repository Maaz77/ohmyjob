"""Words for the guard rules, in many languages: a job page can be in any language.

Every term and every page text goes through norm() first: case folding, no accents on Latin,
Greek and Cyrillic letters, no Arabic and Hebrew vowel marks, one form of alef, yeh and keheh,
one form of apostrophe and dash, one space between words.

How a term matches:
  - a term in a script without spaces between words (Chinese, Japanese, Korean, Thai, Lao,
    Khmer, Myanmar) matches anywhere in the text;
  - another term matches as a whole word or phrase;
  - a term that ends with "*" matches as the start of a word (for languages that add endings);
  - a term that starts with "~" matches anywhere in the text ("~apply" also finds "Reapply").

The lists catch the common names. A button name that no list knows goes to the Haiku check
(judge.py), so the lists do not have to be complete. A wrong word in a block list only blocks
a click (safe); a wrong word in SAFE could let a click through without the Haiku check, so
SAFE holds only plain navigation words and must match the whole name.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Iterable, Optional


# --------------------------------------------------------------------------- normalization

_UNIFY = str.maketrans({
    "’": "'", "‘": "'", "ʼ": "'", "`": "'", "´": "'", "′": "'",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    "ى": "ي",  # alef maqsura -> yeh
    "ی": "ي",  # Persian yeh -> Arabic yeh
    "ک": "ك",  # Persian keheh -> Arabic kaf
    "ـ": None,      # tatweel
    "‍": None,      # zero width joiner
    "​": None,      # zero width space
    "﻿": None,
})


def _strip_mark(base: str, mark: str) -> bool:
    """True if this combining mark is removed: accents on Latin, Greek and Cyrillic letters,
    and Arabic and Hebrew vowel marks (hamza on alef, harakat, shadda, niqqud)."""
    cp = ord(mark)
    if 0x0591 <= cp <= 0x05C7 or 0x064B <= cp <= 0x065F or cp == 0x0670 or 0x06D6 <= cp <= 0x06ED:
        return True
    if not base:
        return False
    b = ord(base)
    return (b < 0x0250 or 0x1E00 <= b <= 0x1EFF        # Latin
            or 0x0370 <= b <= 0x03FF or 0x1F00 <= b <= 0x1FFF  # Greek
            or 0x0400 <= b <= 0x052F)                    # Cyrillic


@lru_cache(maxsize=4096)
def norm(text: Optional[str]) -> str:
    t = unicodedata.normalize("NFKC", str(text or ""))
    t = t.replace("‌", " ").casefold()  # zero width non-joiner (Persian) -> space
    out: list[str] = []
    base = ""
    for ch in unicodedata.normalize("NFKD", t):
        if unicodedata.category(ch).startswith("M"):
            if _strip_mark(base, ch):
                continue
        else:
            base = ch
        out.append(ch)
    t = unicodedata.normalize("NFC", "".join(out)).translate(_UNIFY)
    return re.sub(r"\s+", " ", t).strip()


def _unspaced(term: str) -> bool:
    for ch in term:
        cp = ord(ch)
        if (0x3040 <= cp <= 0x30FF or 0x3400 <= cp <= 0x4DBF or 0x4E00 <= cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF
                or 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF or 0x3130 <= cp <= 0x318F
                or 0x0E00 <= cp <= 0x0EFF or 0x1780 <= cp <= 0x17FF or 0x1000 <= cp <= 0x109F):
            return True
    return False


class Terms:
    """A compiled word list. find() returns the first term found in a text, or None."""

    def __init__(self, *groups: Iterable[str]):
        words, prefixes, anywhere = set(), set(), set()
        for group in groups:
            for raw in group:
                raw = raw.strip()
                if not raw:
                    continue
                if raw.startswith("~"):
                    anywhere.add(norm(raw[1:]))
                elif raw.endswith("*"):
                    term = norm(raw[:-1])
                    (anywhere if _unspaced(term) else prefixes).add(term)
                else:
                    term = norm(raw)
                    (anywhere if _unspaced(term) else words).add(term)
        self.terms = words | prefixes | anywhere
        self._anywhere = sorted(anywhere, key=len, reverse=True)
        parts = []
        if words:
            parts.append(r"(?<!\w)(?:" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")(?!\w)")
        if prefixes:
            parts.append(r"(?<!\w)(?:" + "|".join(re.escape(p) for p in sorted(prefixes, key=len, reverse=True)) + r")")
        self._rx = re.compile("|".join(parts)) if parts else None

    def find(self, text: Optional[str]) -> Optional[str]:
        t = norm(text)
        if not t:
            return None
        if self._rx is not None:
            match = self._rx.search(t)
            if match:
                return match.group(0)
        for term in self._anywhere:
            if term in t:
                return term
        return None

    def __contains__(self, text: str) -> bool:  # "x in TERMS": the whole text is one term
        return bare(text) in self.terms


def bare(text: Optional[str]) -> str:
    """The normalized text without arrows, bullets, punctuation and counts at its ends."""
    t = norm(text)
    t = re.sub(r"^[\W_]+|[\W_]+$", "", t)
    return re.sub(r"\s*\(\d+\)$", "", t).strip()


# --------------------------------------------------------------------------- final controls
# Controls that submit, send, confirm or finish an application. The guard never lets a worker
# click them. (Apply words are separate: they start an application before a fill.)

FINAL = Terms(
    # English
    ["submit", "send", "confirm", "finish", "done", "complete", "finalize", "finalise"],
    # one-click variants of Apply send the application with the stored profile in that single click
    ["~1-click", "~1 click", "~one-click", "~one click", "~quick apply", "~instant apply", "~apply instantly",
     "~apply in one click", "~apply with one click"],
    # Italian
    ["invia", "inviare", "invio", "inoltra", "inoltrare", "conferma", "confermare", "termina", "terminare",
     "concludi", "concludere", "completa", "completare", "fatto", "presenta", "presentare"],
    # French
    ["envoyer", "envoie", "envoyez", "envoi", "soumettre", "soumets", "soumettez", "valider", "valide", "validez",
     "confirmer", "confirme", "confirmez", "terminer", "termine", "terminez", "finaliser", "finalise", "finalisez",
     "transmettre", "transmets"],
    # German
    ["absenden", "senden", "abschicken", "schicken", "einreichen", "ubermitteln", "übermitteln", "bestätigen",
     "bestatigen", "abschließen", "fertigstellen", "fertig", "beenden"],
    # Spanish, Catalan, Galician, Basque
    ["enviar", "envía", "envia", "envío", "presentar", "confirmar", "finalizar", "finaliza", "terminar", "completar",
     "listo", "finalitzar", "finalitza", "acabar", "fet", "rematar", "bidali", "berretsi", "amaitu", "bukatu"],
    # Portuguese
    ["submeter", "submete", "envie", "confirme", "finalize", "concluir", "conclua", "pronto", "concluído"],
    # Dutch, Afrikaans
    ["verzenden", "verzend", "versturen", "verstuur", "indienen", "dien in", "insturen", "stuur", "bevestigen",
     "bevestig", "voltooien", "voltooi", "afronden", "rond af", "klaar", "gereed", "handig in"],
    # Swedish, Danish, Norwegian, Icelandic
    ["skicka", "skicka in", "bekräfta", "slutför", "färdig", "lämna in", "indsend", "bekræft", "afslut", "færdig",
     "udfør", "send inn", "bekreft", "fullfør", "ferdig", "lever søknad", "senda", "staðfesta", "ljúka"],
    # Finnish, Estonian, Latvian, Lithuanian
    ["lähetä*", "vahvista*", "valmis", "viimeistele", "saada", "esita", "kinnita", "lõpeta", "sūtīt", "nosūtīt",
     "iesniegt", "apstiprināt", "pabeigt", "gatavs", "siųsti", "pateikti", "patvirtinti", "užbaigti", "baigti", "atlikta"],
    # Polish, Czech, Slovak, Slovenian, Croatian, Bosnian, Serbian (Latin)
    ["wyślij", "prześlij", "wysłać", "złóż", "zatwierdź", "potwierdź", "zakończ", "gotowe", "odeslat", "odeslání",
     "potvrdit", "dokončit", "hotovo", "podat", "odoslať", "potvrdiť", "dokončiť", "podať", "pošlji", "oddaj",
     "potrdi", "dokončaj", "končaj", "končano", "pošalji", "predaj", "potvrdi", "završi", "dovrši", "gotovo"],
    # Serbian (Cyrillic), Macedonian, Bulgarian, Russian, Ukrainian, Belarusian
    ["пошаљи", "предај", "потврди", "заврши", "готово", "испрати", "поднеси", "изпрати", "изпращане", "подай",
     "потвърди", "завърши", "отправить", "отправка", "отправь", "подать", "подтвердить", "завершить",
     "надіслати", "відправити", "подати", "підтвердити", "завершити", "адправіць", "падаць", "пацвердзіць",
     "завяршыць", "гатова"],
    # Romanian, Hungarian
    ["trimite", "trimiteți", "depune", "confirmă", "finalizează", "termină", "gata", "küld*", "elküld*", "beküld*",
     "benyújt*", "megerősít*", "befejez*", "véglegesít*", "kész"],
    # Greek, Turkish, Azerbaijani, Albanian, Maltese, Irish, Welsh
    ["υποβολή", "υποβάλετε", "αποστολή", "στείλτε", "επιβεβαίωση", "επιβεβαίωσε", "ολοκλήρωση", "ολοκληρώστε",
     "τέλος", "έγινε", "gönder*", "onayla*", "tamamla*", "bitir*", "bitti", "göndər*", "təsdiqlə*",
     "dërgo", "paraqit", "konfirmo", "përfundo", "ibgħat", "ikkonferma", "lest", "seol", "cuir isteach",
     "deimhnigh", "críochnaigh", "anfon", "cyflwyno", "cadarnhau", "gorffen"],
    # Georgian, Armenian, Kazakh, Uzbek
    ["გაგზავნა", "დადასტურება", "დასრულება", "ուղարկել", "հաստատել", "ավարտել", "жіберу", "растау", "аяқтау",
     "yuborish", "tasdiqlash", "yakunlash"],
    # Arabic, Hebrew, Persian, Urdu
    ["إرسال", "أرسل", "تقديم", "قدم", "تأكيد", "أكد", "إنهاء", "إتمام", "تم", "שלח", "שליחה", "הגש", "הגשה", "אשר",
     "אישור", "סיום", "סיים", "فرستادن", "ثبت", "تأیید", "تایید", "پایان", "اتمام", "بھیجیں", "جمع کرائیں", "تصدیق",
     "مکمل"],
    # Hindi, Marathi, Bengali, Tamil, Telugu
    ["भेजें", "सबमिट", "जमा करें", "पुष्टि", "पूरा करें", "समाप्त", "पाठवा", "पुष्टी", "পাঠান", "জমা দিন", "নিশ্চিত",
     "সম্পন্ন", "அனுப்பு", "சமர்ப்பி", "உறுதிப்படுத்து", "పంపు", "సమర్పించు"],
    # Chinese, Japanese, Korean, Thai
    ["提交", "送出", "发送", "發送", "传送", "傳送", "确认", "確認提交", "完成", "送信", "提出", "確定", "完了", "終了",
     "제출", "전송", "보내기", "완료", "ส่ง", "ยืนยัน", "เสร็จสิ้น"],
    # Vietnamese, Indonesian, Malay, Filipino, Swahili
    ["gửi", "nộp", "xác nhận", "hoàn tất", "hoàn thành", "xong", "kirim", "ajukan", "konfirmasi", "selesai",
     "selesaikan", "hantar", "serahkan", "sahkan", "ipadala", "isumite", "kumpirmahin", "tapusin", "tapos na",
     "tuma", "wasilisha", "thibitisha", "maliza", "kamilisha"],
)

# Apply words: allowed to open an application, blocked in a tab after its first fill.
APPLY = Terms(
    ["~apply"],
    ["candidati", "candidatura", "candidarsi", "candidarti", "candidatevi", "postuler", "postulez", "je postule",
     "candidater", "bewerb*", "aplicar", "aplica", "postular*", "postúlate", "inscribir*", "solicitar", "solicita",
     "candidatar*", "candidate-se", "inscrever*", "sol·licitar", "solliciteer", "solliciteren", "reageer", "reageren",
     "doen aansoek", "aplikuj", "aplikować", "złóż aplikację", "ansök", "sök tjänsten", "sök jobbet", "ansøg",
     "søg jobbet", "søg stillingen", "søk nå", "søk på stillingen", "søk stillingen", "sækja um", "hae", "hae nyt",
     "hae paikkaa", "kandideeri", "pieteikties", "kandidatuoti", "teikti paraišką", "reagovat", "reagovať",
     "uchádzať sa", "prijavi se", "apliciraj", "пријави се", "кандидатствай", "кандидатствайте", "аплицирај",
     "откликнуться", "отклик", "подать заявку", "відгукнутися", "подати заявку", "aplică", "aplicați",
     "candidează", "jelentkez*", "αίτηση", "başvur*", "apliko", "applika", "déan iarratas", "gwneud cais"],
    ["تقدم", "التقديم", "قدم الآن", "قدم طلب", "הגש מועמדות", "הגשת מועמדות", "درخواست", "درخواست همکاری",
     "درخواست دیں", "आवेदन", "আবেদন", "申请", "申請", "投递", "投遞", "应聘", "應徵", "応募", "エントリー", "지원",
     "สมัคร", "ứng tuyển", "lamar", "melamar", "mohon", "memohon", "mag-apply", "mag-aplay", "omba", "tuma maombi"],
)

# Consent words. A worker may click them on data-privacy and terms dialogs, but never on a
# cookie banner (only the most private choice) and never as a sign-in permission.
CONSENT = Terms(
    ["accept", "agree", "consent", "i agree", "i accept", "allow", "authorize", "authorise"],
    ["accetta", "accetto", "accettare", "acconsento", "consenso", "sono d'accordo", "consenti", "accepter", "accepte",
     "j'accepte", "acceptez", "je suis d'accord", "consentir", "autoriser", "akzeptieren", "akzeptiere", "annehmen",
     "zustimmen", "ich stimme zu", "einverstanden", "erlauben", "zulassen", "aceptar", "acepto", "estoy de acuerdo",
     "consiento", "permitir", "aceitar", "aceito", "concordo", "estou de acordo", "consinto", "acceptar", "accepto",
     "estic d'acord", "permetre", "accepteren", "accepteer", "akkoord", "ga akkoord", "ik ga akkoord", "toestemming",
     "toestaan", "acceptera", "godkänn", "jag godkänner", "samtycker", "tillåt", "acceptér", "godkend",
     "jeg accepterer", "samtykke", "tillad", "aksepter", "godta", "godkjenn", "samtykker", "tillat", "samþykkja",
     "samþykki", "hyväksy*", "salli", "suostun", "nõustu", "nõustun", "aktsepteeri", "luba", "piekrītu", "pieņemt",
     "akceptēt", "atļaut", "sutinku", "priimti", "sutikti", "leisti", "akceptuj", "akceptuję", "zgadzam się",
     "wyrażam zgodę", "zgoda", "zezwól", "přijmout", "souhlasím", "souhlas", "přijímám", "povolit", "prijať",
     "súhlasím", "povoliť", "sprejmi", "sprejmem", "strinjam se", "dovoli", "prihvati", "prihvaćam", "slažem se",
     "suglasan", "dopusti", "dozvoli", "elfogad*", "egyetért*", "hozzájárul*", "engedélyez*", "acceptă",
     "sunt de acord", "de acord", "permite"],
    ["приеми", "приемам", "съгласен съм", "съгласие", "разреши", "прифати", "се согласувам", "дозволи", "принять",
     "принимаю", "согласен", "я согласен", "согласие", "разрешить", "прийняти", "приймаю", "погоджуюсь", "згоден",
     "згода", "дозволити", "αποδοχή", "αποδέχομαι", "συμφωνώ", "επιτρέπεται", "kabul et", "kabul ediyorum",
     "onaylıyorum", "izin ver", "pranoj", "pajtohem", "lejo", "naqbel", "aontaím", "cytuno", "caniatáu"],
    ["قبول", "أوافق", "موافق", "أقبل", "السماح", "اسمح", "אני מסכים", "אני מסכימה", "מסכים", "מסכימה", "אני מאשר",
     "מקבל", "אפשר", "לאפשר", "پذیرفتن", "موافقم", "می پذیرم", "اجازه", "متفق", "स्वीकार", "सहमत", "अनुमति",
     "গ্রহণ", "সম্মত", "অনুমতি", "接受", "同意", "允许", "允許", "授权", "授權", "承諾", "受け入れる", "許可", "동의",
     "수락", "허용", "ยอมรับ", "ยินยอม", "อนุญาต", "chấp nhận", "đồng ý", "cho phép", "terima", "setuju",
     "saya setuju", "menerima", "izinkan", "benarkan", "tanggapin", "sumasang-ayon", "payagan", "kubali",
     "nakubali", "ruhusu"],
)

# Sign-in permission words: Allow and Authorize. Never allowed for a worker.
PERMISSION = Terms(
    ["allow", "authorize", "authorise", "consenti", "autorizza", "autorizzare", "autoriser", "autorise", "erlauben",
     "zulassen", "autorisieren", "permitir", "autorizar", "autorizo", "permetre", "toestaan", "autoriseren",
     "machtigen", "tillåt", "tillad", "tillat", "salli", "luba", "atļaut", "leisti", "zezwól", "autoryzuj",
     "povolit", "povoliť", "dovoli", "dopusti", "dozvoli", "engedélyez*", "permite", "autoriza", "lejo", "caniatáu"],
    ["разреши", "разрешить", "дозволи", "дозволити", "авторизовать", "авторизувати", "επιτρέπεται", "επίτρεψε",
     "izin ver", "yetkilendir*"],
    ["السماح", "اسمح", "سماح", "אפשר", "לאפשר", "اجازه", "اجازت", "अनुमति", "অনুমতি", "允许", "允許", "授权", "授權",
     "許可", "허용", "อนุญาต", "cho phép", "ủy quyền", "izinkan", "benarkan", "payagan", "ruhusu"],
)

# Words that mark a cookie banner (on the button or on the lines near it).
COOKIE = Terms(
    ["cookie*", "biscotti", "témoins", "galletas", "kakor", "informasjonskapsler", "eväste*", "küpsis*",
     "sīkdatn*", "slapuk*", "ciasteczk*", "piškotk*", "kolačić*", "sütik", "süti*", "бисквитк*", "куки", "кукис*",
     "çerez*", "μπισκότα*"],
    ["ملفات تعريف الارتباط", "تعريف الارتباط", "עוגיות", "کوکی", "कुकी", "কুকি", "クッキー", "쿠키", "คุกกี้",
     "小型文字檔"],
)

# Sign-up and account creation. Allowed only when the control is a Google option.
SIGNUP = Terms(
    ["sign up", "signup", "create account", "create an account", "create your account", "register", "new account"],
    ["registrati", "registrazione", "crea account", "crea un account", "iscriviti", "crea il tuo account",
     "s'inscrire", "inscription", "créer un compte", "créez votre compte", "inscrivez-vous", "registrieren",
     "registrierung", "konto erstellen", "konto anlegen", "jetzt registrieren", "neues konto", "registrarse",
     "regístrate", "registro", "crear cuenta", "crear una cuenta", "crea tu cuenta", "registar", "registrar",
     "registe-se", "cadastre-se", "cadastrar", "cadastro", "criar conta", "crie sua conta", "crie a sua conta",
     "registrar-se", "crear un compte", "crea un compte", "registreren", "registreer", "account aanmaken",
     "maak een account", "registrera", "skapa konto", "skapa ett konto", "registrer", "opret konto", "opret profil",
     "opret bruger", "opprett konto", "opprett profil", "opprett bruker", "rekisteröidy", "rekisteröinti",
     "luo tili", "registreeru", "loo konto", "reģistrēties", "izveidot kontu", "registruotis", "sukurti paskyrą",
     "zarejestruj*", "rejestracja", "utwórz konto", "załóż konto", "registrovat", "registrace", "vytvořit účet",
     "zaregistrovat", "registrovať", "registrácia", "vytvoriť účet", "registracija", "registriraj", "registruj",
     "kreiraj račun", "izradi račun", "napravi nalog", "otvori račun", "ustvari račun", "regisztráció",
     "regisztrál*", "fiók létrehozása", "înregistrare", "înregistrează-te", "creează cont", "creare cont"],
    ["регистрација", "региструј", "направи налог", "регистрация", "регистрирай", "създай профил", "създай акаунт",
     "зарегистрироваться", "создать аккаунт", "создать учетную запись", "создать профиль", "реєстрація",
     "зареєструватися", "створити акаунт", "створити обліковий запис", "εγγραφή", "δημιουργία λογαριασμού",
     "kaydol", "kayıt ol", "üye ol", "hesap oluştur", "regjistrohu", "krijo llogari"],
    ["تسجيل", "إنشاء حساب", "اشترك", "سجل الآن", "הרשמה", "הירשם", "צור חשבון", "יצירת חשבון", "ثبت نام",
     "ایجاد حساب", "पंजीकरण", "रजिस्टर करें", "साइन अप", "खाता बनाएं", "注册", "註冊", "创建账户", "创建帐户",
     "建立帳戶", "创建账号", "新規登録", "会員登録", "アカウント作成", "アカウントを作成", "회원가입", "가입하기",
     "계정 만들기", "계정 생성", "ลงทะเบียน", "สมัครสมาชิก", "สร้างบัญชี", "đăng ký", "tạo tài khoản", "daftar",
     "buat akun", "daftar sekarang", "cipta akaun", "mag-sign up", "magrehistro", "gumawa ng account", "jisajili",
     "fungua akaunti"],
)

# Delete and withdraw.
DELETE = Terms(
    ["delete", "withdraw", "elimina", "eliminare", "cancella", "cancellare", "ritira", "ritirare", "supprimer",
     "supprime", "supprimez", "retirer", "löschen", "zurückziehen", "eliminar", "borrar", "retirar", "excluir",
     "apagar", "esborrar", "verwijderen", "verwijder", "intrekken", "trek in", "radera", "dra tillbaka", "slet",
     "træk tilbage", "slett", "trekk tilbake", "poista", "kustuta", "võta tagasi", "dzēst", "atsaukt", "ištrinti",
     "atšaukti", "usuń", "wycofaj", "smazat", "vymazať", "zmazať", "izbriši", "umakni", "obriši", "povuci",
     "șterge", "retrage", "töröl*", "visszavon*"],
    ["обриши", "избриши", "повуци", "изтрий", "оттегли", "повлечи", "удалить", "отозвать", "видалити", "відкликати",
     "выдаліць", "διαγραφή", "απόσυρση", "sil", "geri çek", "fshi"],
    ["حذف", "سحب", "امسح", "מחק", "מחיקה", "משוך", "پاک کردن", "हटाएं", "मिटाएं", "删除", "刪除", "撤回", "撤销",
     "撤銷", "削除", "取り下げ", "辞退", "삭제", "철회", "ลบ", "ถอน", "xóa", "rút lại", "hapus", "tarik kembali",
     "padam", "tarik balik"],
)

# Search and filter boxes (a job search on a careers site). A fill there is not an application form: a search for the
# job title must not block the "Apply" button that opens the form.
SEARCH = Terms(
    ["search*", "filter*", "keyword*", "find jobs", "find a job"],
    ["cerca", "ricerca", "recherche*", "rechercher", "suche*", "suchen", "buscar", "búsqueda", "busca", "pesquisa*", "procurar",
     "zoek*", "sök", "søg", "søk", "szukaj", "wyszukaj", "hledat", "vyhledat", "keres*", "căutare", "caută"],
    ["поиск", "найти", "пошук", "αναζήτηση", "ara", "بحث", "חיפוש", "جستجو", "खोज", "検索", "搜索", "搜尋", "검색",
     "ค้นหา", "tìm kiếm", "cari", "pencarian"],
)

# A Google option on a site ("Sign in with Google", "Continuer avec Google" ...).
GOOGLE = Terms(["~google", "гугл", "גוגל", "جوجل", "گوگل", "谷歌", "グーグル", "구글"])

# Messages that an application was sent (the checks raise an alarm when the last page shows one).
SUBMITTED = Terms(
    ["application was sent", "application has been sent", "application sent", "application submitted",
     "application was submitted", "application has been submitted", "thank you for applying",
     "thanks for applying", "thank you for your application", "your application has been received",
     "we've received your application", "we have received your application"],
    ["candidatura inviata", "candidatura è stata inviata", "grazie per la tua candidatura", "grazie per la candidatura",
     "abbiamo ricevuto la tua candidatura", "candidatura ricevuta", "candidature envoyée",
     "candidature a été envoyée", "merci pour votre candidature", "merci d'avoir postulé",
     "nous avons bien reçu votre candidature", "bewerbung wurde gesendet", "bewerbung wurde abgeschickt",
     "bewerbung wurde übermittelt", "bewerbung ist eingegangen", "vielen dank für ihre bewerbung",
     "danke für ihre bewerbung", "danke für deine bewerbung", "solicitud enviada", "solicitud ha sido enviada",
     "gracias por tu solicitud", "gracias por su solicitud", "gracias por postularte", "hemos recibido tu solicitud",
     "candidatura enviada", "candidatura foi enviada", "obrigado pela sua candidatura", "obrigado por se candidatar",
     "recebemos a sua candidatura", "recebemos sua candidatura", "sollicitatie is verzonden",
     "sollicitatie is verstuurd", "bedankt voor je sollicitatie", "bedankt voor uw sollicitatie",
     "aplikacja została wysłana", "dziękujemy za aplikację", "dziękujemy za zgłoszenie", "ansökan har skickats",
     "tack för din ansökan", "ansøgning er sendt", "tak for din ansøgning", "søknaden er sendt", "takk for søknaden",
     "hakemus on lähetetty", "kiitos hakemuksestasi", "žádost byla odeslána", "děkujeme za vaši žádost",
     "candidatura a fost trimisă", "aplicația a fost trimisă", "köszönjük a jelentkezését", "jelentkezés elküldve",
     "başvurunuz alındı", "başvurunuz gönderildi"],
    ["отклик отправлен", "заявка отправлена", "спасибо за отклик", "спасибо за заявку", "відгук надіслано",
     "заявку надіслано", "η αίτησή σας υποβλήθηκε", "ευχαριστούμε για την αίτησή σας", "تم إرسال طلبك",
     "تم استلام طلبك", "شكرا لتقديمك", "המועמדות נשלחה", "תודה על הגשת המועמדות", "申请已提交", "申请已发送",
     "感谢您的申请", "申請已提交", "感謝您的申請", "応募が完了しました", "ご応募ありがとうございます",
     "応募を受け付けました", "지원이 완료되었습니다", "지원해 주셔서 감사합니다", "lamaran anda telah dikirim",
     "terima kasih telah melamar"],
)

# Plain navigation names. A click on a control whose WHOLE name is one of these needs no Haiku check.
SAFE = Terms(
    ["next", "continue", "back", "previous", "review", "save", "save and continue", "save & continue",
     "continue to next step", "next step", "review your application", "upload", "upload resume", "upload cv",
     "choose file", "select file", "attach", "browse", "add", "add another", "edit", "remove", "close", "cancel",
     "dismiss", "show more", "see more", "show less", "see less", "expand", "collapse", "yes", "no", "skip"],
    ["avanti", "successivo", "continua", "prosegui", "indietro", "precedente", "salva", "salva e continua", "carica",
     "scegli file", "sfoglia", "aggiungi", "modifica", "rimuovi", "chiudi", "annulla", "mostra altro", "sì", "si",
     "suivant", "continuer", "poursuivre", "précédent", "retour", "enregistrer", "enregistrer et continuer",
     "importer", "parcourir", "ajouter", "modifier", "fermer", "annuler", "afficher plus", "voir plus", "oui", "non",
     "weiter", "fortfahren", "zurück", "speichern", "speichern und weiter", "hochladen", "datei auswählen",
     "durchsuchen", "hinzufügen", "bearbeiten", "entfernen", "schließen", "abbrechen", "mehr anzeigen", "ja", "nein",
     "siguiente", "continuar", "atrás", "anterior", "volver", "revisar", "guardar", "guardar y continuar", "subir",
     "cargar", "seleccionar archivo", "examinar", "añadir", "agregar", "editar", "quitar", "cerrar", "cancelar",
     "ver más", "mostrar más", "sí", "próximo", "seguinte", "avançar", "voltar", "salvar", "carregar",
     "escolher ficheiro", "escolher arquivo", "adicionar", "remover", "fechar", "ver mais", "sim", "não",
     "següent", "enrere", "desa", "desar", "puja", "afegeix", "edita", "tanca"],
    ["volgende", "doorgaan", "verder", "terug", "vorige", "opslaan", "uploaden", "bestand kiezen", "bladeren",
     "toevoegen", "bewerken", "wijzigen", "sluiten", "annuleren", "meer weergeven", "nee", "nästa", "fortsätt",
     "tillbaka", "föregående", "spara", "ladda upp", "välj fil", "lägg till", "redigera", "stäng", "avbryt", "nej",
     "næste", "fortsæt", "tilbage", "forrige", "gem", "vælg fil", "tilføj", "rediger", "luk", "annuller", "neste",
     "fortsett", "tilbake", "lagre", "last opp", "velg fil", "legg til", "lukk", "nei", "seuraava", "jatka",
     "takaisin", "edellinen", "tallenna", "valitse tiedosto", "lisää", "muokkaa", "sulje", "peruuta", "kyllä", "ei",
     "dalej", "kontynuuj", "wstecz", "wróć", "poprzedni", "zapisz", "wybierz plik", "dodaj", "edytuj", "zamknij",
     "anuluj", "tak", "nie", "další", "pokračovat", "zpět", "předchozí", "uložit", "nahrát", "vybrat soubor",
     "přidat", "upravit", "zavřít", "zrušit", "ano", "ne", "ďalej", "pokračovať", "späť", "uložiť", "nahrať",
     "pridať", "upraviť", "zavrieť", "zrušiť", "áno", "tovább", "folytatás", "vissza", "mentés", "feltöltés",
     "hozzáadás", "szerkesztés", "bezárás", "mégse", "igen", "nem", "următorul", "continuă", "înapoi", "salvează",
     "încarcă", "adaugă", "editează", "închide", "anulează", "da", "nu", "dalje", "sljedeće", "sledeće", "nastavi",
     "natrag", "nazad", "spremi", "sačuvaj", "učitaj", "uredi", "zatvori", "odustani", "otkaži", "naprej",
     "nadaljuj", "shrani", "naloži", "zapri", "prekliči"],
    ["напред", "продължи", "назад", "запази", "качи", "добави", "редактирай", "затвори", "отказ", "да", "не",
     "далее", "продолжить", "сохранить", "загрузить", "добавить", "изменить", "редактировать", "закрыть",
     "отмена", "отменить", "нет", "далі", "продовжити", "зберегти", "завантажити", "додати", "редагувати",
     "закрити", "скасувати", "так", "ні", "επόμενο", "συνέχεια", "πίσω", "αποθήκευση", "μεταφόρτωση", "προσθήκη",
     "επεξεργασία", "κλείσιμο", "ακύρωση", "ναι", "όχι", "ileri", "devam", "devam et", "geri", "önceki", "kaydet",
     "yükle", "ekle", "düzenle", "kapat", "iptal", "vazgeç", "evet", "hayır"],
    ["التالي", "متابعة", "استمرار", "رجوع", "السابق", "حفظ", "تحميل", "رفع", "إضافة", "تعديل", "إغلاق", "إلغاء",
     "نعم", "لا", "הבא", "המשך", "הקודם", "חזרה", "שמור", "העלה", "הוסף", "ערוך", "סגור", "ביטול", "כן", "לא",
     "بعدی", "ادامه", "بازگشت", "قبلی", "ذخیره", "بارگذاری", "افزودن", "ویرایش", "بستن", "لغو", "بله", "خیر",
     "अगला", "जारी रखें", "वापस", "पिछला", "सहेजें", "अपलोड", "जोड़ें", "बंद करें", "रद्द करें", "हाँ", "नहीं"],
    ["下一步", "继续", "繼續", "上一步", "返回", "保存", "儲存", "上传", "上傳", "添加", "新增", "编辑", "編輯", "关闭",
     "關閉", "取消", "是", "否", "次へ", "次に進む", "続ける", "戻る", "前へ", "アップロード", "追加", "編集", "閉じる",
     "キャンセル", "はい", "いいえ", "다음", "계속", "이전", "뒤로", "저장", "업로드", "추가", "편집", "수정", "닫기",
     "취소", "예", "아니요", "네", "ถัดไป", "ต่อไป", "ดำเนินการต่อ", "ย้อนกลับ", "ก่อนหน้า", "บันทึก", "อัปโหลด",
     "เพิ่ม", "แก้ไข", "ปิด", "ยกเลิก", "ใช่", "ไม่ใช่"],
    ["tiếp", "tiếp tục", "tiếp theo", "quay lại", "trở lại", "lưu", "tải lên", "thêm", "chỉnh sửa", "đóng", "hủy",
     "có", "không", "berikutnya", "selanjutnya", "lanjut", "lanjutkan", "kembali", "sebelumnya", "simpan",
     "unggah", "tambah", "ubah", "tutup", "batal", "ya", "tidak", "seterusnya", "teruskan", "muat naik", "sunting",
     "susunod", "magpatuloy", "bumalik", "i-save", "i-upload", "idagdag", "i-edit", "isara", "kanselahin", "oo",
     "hindi", "inayofuata", "endelea", "rudi", "hifadhi", "pakia", "ongeza", "hariri", "funga", "ghairi", "ndiyo",
     "hapana"],
)
