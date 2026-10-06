# Security

remcycle reads what you typed into Claude Code and keeps it on your machine, so a flaw in it can expose private text. Reports are welcome.

## Reporting a problem

Report it privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**. Please do not open a public issue for something that could expose someone's data.

remcycle has one maintainer, so an answer can take a few days.

## What counts

- The archive, or the dream's copy of memory, being readable by another user or sent off the machine.
- A secret that the redaction is documented to catch ending up in the archive.
- A session or a memory file causing remcycle to run a command, or to write outside its own folders.
- Text in a session getting remcycle to store something as if you had said it.

## What is already known

- Redaction is pattern matching, and a secret with no recognisable shape is stored as written. [What the archive holds, and what protects it](docs/reference.md#what-the-archive-holds-and-what-protects-it) says what is covered.
- The dream sends the prose of your sessions and, for a project with unfinished work, the messages of its recent commits and the titles and descriptions of its merged pull requests to the model, through your own Claude Code. That is how it works. The mod starts that dream once a day, and `dream daily off` turns it off.
