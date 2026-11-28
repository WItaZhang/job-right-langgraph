"""Expected, user-facing errors. The CLI prints these without a traceback."""


class JobRightError(Exception):
    """Base class for errors a user can act on."""


class NotInitialized(JobRightError):
    pass


class AlreadyInitialized(JobRightError):
    pass


class UnknownProposal(JobRightError, LookupError):
    pass


class NothingToDecide(JobRightError, LookupError):
    pass


class CandidateMismatch(JobRightError):
    """An approval named a candidate other than the one stored for review."""


class ProposalIdInUse(JobRightError, ValueError):
    pass


class InvalidProposalId(JobRightError, ValueError):
    pass


class InvalidModelSpec(JobRightError, ValueError):
    pass
