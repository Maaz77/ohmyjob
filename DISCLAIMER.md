# Disclaimer

ohmyjob is a personal automation tool. Read this before you use it.

## Job-board terms and account risk

- Job boards and employer sites restrict automated access in their terms of service. Searching them with a scraper, and filling forms with an automated browser, may break those terms. A board may rate-limit you, block you, ask for a CAPTCHA, or suspend your account. That risk is yours.
- The defaults are conservative (one day of postings, 30 results per term, five workers), and the plugin is built never to press a final Submit button (the hooks refuse it in the main session and in every agent). One gap: until `/ohmyjob:setup` has created the Python environment, the main session's own Chrome guard lets calls through with a notice, so run setup before you let Claude touch your browser. None of that makes automated use acceptable to a given site. Check the terms of every site you use, and lower the settings if a site pushes back.
- Applications are written from your own `Profile.md` and resumes. You are responsible for every statement in an application you submit. Review each form before you press Submit.

## Where your data goes

- **Job sites.** Searches go to the boards you list in `search.sources`. The browser steps use your own signed-in Chrome profile, so the sites see you as you.
- **Employers and application systems.** Filling a form sends your resume and answers to the employer's site and its application system before any Submit; many of them keep a draft. Review and delete drafts you do not want kept.
- **Anthropic.** The text the agents work with (your `Profile.md`, your resumes, job postings, page contents, form fields and your answers) is sent to Anthropic through Claude Code and Claude in Chrome, under your Claude plan and its terms and privacy policy. A short classification call for unknown button names is sent the same way.
- **Local files.** Everything else stays on your computer: the tracker, the job folders, the PDFs, the run state in `.ohmyjob/`, and the plugin's data folder under `~/.claude/plugins/data/`.

## Keep your workspace private

The workspace holds personal data (name, contact details, work history, application answers). **Never commit it to a public repository.** Setup writes a `.gitignore` for the workspace; check it before you put the folder under version control, and keep the repository private. The `git_snapshot` setting force-adds the tracker and `Applications/` to git even though they are ignored: never use it in a repository that has a public remote.

## No warranty

The software is provided "as is", without warranty of any kind, as stated in the [MIT license](LICENSE). The authors are not liable for rejected or missed applications, wrong or incomplete form entries, account actions taken by a site, costs of your Claude plan, or any other loss. Software and sites change: always check the result yourself.
