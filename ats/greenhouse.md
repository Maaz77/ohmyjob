# Greenhouse (greenhouse.io, or a Greenhouse form on a company page)
hosts: *.greenhouse.io

- The form can sit in a cross-origin iframe on the company's own page, which read_page cannot see into. You then see only the site header.
  - A company page with `?gh_jid=N` in its address: when you navigate there, the guard sends your tab to `https://boards.greenhouse.io/embed/job_app?token=N` instead, where the job description and the form are on one page. Fill it there.
  - The same happens on a page with `?gh_src=...` whose address ends in the posting number, for example `https://stripe.com/careers/apply/software-engineer-payments-and-risk/6717520?gh_src=73vnei`.
  - On any other company page (for example `.../software-engineer-talent-tooling/apply?gh_src=...`, whose address carries no posting number), look for a link `"[AA frame]"` next to the empty area: the helper extension puts the frame's own address in its `href`, such as `https://job-boards.greenhouse.io/embed/job_app?for=<org>&validityToken=...&token=N`. Navigate your tab to that address and fill the form there. The address is long and every part of it counts: take the whole `href`. It is the frame's address now, not only the one the page started it on.
  - If the fields stay empty after 2 attempts and no `[AA frame]` link is there, stop with status "blocked", blocker `NOT_ACCESSIBLE: Greenhouse iframe`.
- The form can carry a reCAPTCHA above the submit button. A CAPTCHA is a blocker: stop with status "blocked", blocker `CAPTCHA: a reCAPTCHA above the submit button`.
- The form is usually one page: name, email, country, phone, resume, profile links, a work-authorization question and a privacy acknowledgement. Tick the acknowledgement and list it in `declarations`.
