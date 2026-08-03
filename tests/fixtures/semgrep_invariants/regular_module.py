"""Non-provider scope fixture: provider-only rules must not report this file."""


def out_of_scope(factory):
    command = ("semgrep", "--autofix", "--fix")
    return factory(command, mutation_authority=True)
