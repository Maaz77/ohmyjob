# Greenhouse (greenhouse.io, or a Greenhouse form on a company page)

- The form can sit in a cross-origin iframe on the company's own page, which read_page cannot see into. You then see only the site header.
  - A company page with `?gh_jid=N` in its address: when you navigate there, the guard sends your tab to `https://boards.greenhouse.io/embed/job_app?token=N` instead, where the job description and the form are on one page. Fill it there.
  - If the fields stay empty after 2 attempts, stop with status "blocked", blocker "Greenhouse form in a cross-origin iframe".
- The form can carry a reCAPTCHA above the submit button. A CAPTCHA is a blocker: stop with status "blocked".
- The form is usually one page: name, email, country, phone, resume, profile links, a work-authorization question and a privacy acknowledgement. Tick the acknowledgement and list it in `declarations`.
