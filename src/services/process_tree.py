"""Windows job ownership for subprocess trees, without external process-list commands."""

import asyncio
import ctypes


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IOCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IOCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", ctypes.c_uint32),
        ("TotalProcesses", ctypes.c_uint32),
        ("ActiveProcesses", ctypes.c_uint32),
        ("TotalTerminatedProcesses", ctypes.c_uint32),
    ]


class WindowsProcessJob:
    """Assign a worker before sending stdin; descendants inherit this non-breakaway job."""

    def __init__(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel = kernel
        self._handle = None
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, ctypes.c_wchar_p], ctypes.c_void_p),
            "SetInformationJobObject": (
                [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32],
                ctypes.c_int,
            ),
            "QueryInformationJobObject": (
                [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p],
                ctypes.c_int,
            ),
            "OpenProcess": ([ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32], ctypes.c_void_p),
            "AssignProcessToJobObject": ([ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
            "TerminateJobObject": ([ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
            "CloseHandle": ([ctypes.c_void_p], ctypes.c_int),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(kernel, name)
            function.argtypes = arguments
            function.restype = result
        self._handle = self._check(kernel.CreateJobObjectW(None, None))
        try:
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
            self._check(
                kernel.SetInformationJobObject(
                    self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                )
            )
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _check(value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def assign(self, pid: int) -> None:
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE, required by AssignProcessToJobObject.
        process = self._check(self._kernel.OpenProcess(0x0101, False, pid))
        try:
            self._check(self._kernel.AssignProcessToJobObject(self._handle, process))
        finally:
            self._kernel.CloseHandle(process)

    async def terminate(self) -> None:
        """Wait until every descendant has terminated before media files are removed."""
        self._check(self._kernel.TerminateJobObject(self._handle, 1))
        async with asyncio.timeout(5):
            while True:
                accounting = _Accounting()
                self._check(
                    self._kernel.QueryInformationJobObject(
                        self._handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
                    )
                )
                if not accounting.ActiveProcesses:
                    return
                await asyncio.sleep(0.01)

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            self._kernel.CloseHandle(handle)
