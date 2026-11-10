# Workday (*.myworkdayjobs.com)
hosts: *.myworkdayjobs.com

- Step 1 is often "Create Account/Sign In". Creating an account with an email and a password, or typing a password, is not allowed.
  - If the page offers a Google option, use it (see "Sign in with Google" in your rules).
  - Otherwise stop with status "blocked", blocker `ACCOUNT_WALL: Workday, no Google option`.
- After sign-in, the steps are usually: My Information, My Experience, Application Questions, Voluntary Disclosures, Review.
- Country can default to the job's country. Set it to the candidate's country/region from Profile.md. Changing the country can clear the name fields: read the page again and refill them.
- A Province or State list may not contain the candidate's exact city. Choose the region that contains it only when that region is in the list, and write the choice in `filled`.
- An e-signature or "I certify that I have read..." checkbox is a declaration. Tick it and list it in `declarations`.
- The Review step shows the final Submit button. Stop there with status "parked".
- Required questions are marked with `*` in their label. Before you fill a step, read it once with `filter: "all"` and look at its required questions. Answer a common question (for example "Have you worked for <company> before?") from "Follow Up Questions", else in the most natural way and list the answer in `drafted`, as your rules say. If it is a technical or personal question that the brief does not answer (for example "Phone Device Type"), stop at once: status "blocked", blocker `QUESTIONS: questions for the candidate`, each such question in `questions` (see "How to answer" in your rules). Do not fill the rest of that step. Save and Continue does not work while a required question is empty.
- Search lists ("How Did You Hear About Us?", Province, Country Phone Code) are not plain selects. To answer one: click the field by ref, call read_page alone, click the option by ref, then call read_page again before any other click. A list can open a second level (for example "Job Board" > "LinkedIn"): read again and click the option there. Refs change after every click in these lists, so never put two of these clicks in one browser_batch.
