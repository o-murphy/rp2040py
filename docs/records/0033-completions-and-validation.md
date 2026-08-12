# 0033. Add autocompletions for the cli tool

* Status: Implemented
* Conceived: 2026-08-12

## Context

As the `rp2040py` CLI tool grows in complexity with multiple subcommands (`run`, `micropython`, `kaluma`, `bench`, `mklittlefs`, `mpremote`) and various flags (such as `--board`, `--log-level`, `--littlefs`), users need a more efficient way to interact with it in shells like Bash and Zsh without manually memorizing every option or argument.

## Decision

* Integrate the pure-Python **`argcomplete`** library to provide out-of-the-box tab-completion support for `argparse`.
* Add the required `# PYTHON_ARGCOMPLETE_OK` magic comment to the entry point and call `argcomplete.autocomplete(parser)` before parsing arguments.
* Introduce a new `install-completion` subcommand (`_cmd_install_completion`) that automates appending the shell initialization hook (`eval "$(register-python-argcomplete rp2040py)"`) to `~/.bashrc` or `~/.zshrc`.
* Implement robust file suffix validators (`_mk_file_suffixes_validator`) to restrict file inputs like `--littlefs`, `--fat12`, and local scripts to valid extensions (`.img`, `.bin`, `.lfs`, `.py`, `.js`).

## Consequences

* **Pros:**
* Seamless tab-completion for all subcommands, options, and choices (such as `--board` and `--log-level`).
* Improved developer experience via a simple `rp2040py install-completion` command.
* Zero external compiled dependencies (maintaining pure Python footprint).


* **Cons:**
* Users must manually run `rp2040py install-completion` once and reload their shell to activate the feature.



## Migration mapping

* No breaking changes for existing CLI invocations.
* Optional addition of `argcomplete` to dependencies for full completion support.
