from types import SimpleNamespace

from mcrit_similarity.backend import BinjaSmdaInterface


class FakeBlock:
    """Not iterable on purpose: iterating a real BasicBlock renders disassembly text."""

    def __init__(self, start, lengths, successors=()):
        self.start = start
        self.end = start + sum(lengths)
        self.arch = None
        self.outgoing_edges = [
            SimpleNamespace(target=SimpleNamespace(start=addr)) for addr in successors
        ]


class FakeFunction:
    def __init__(self, start, name="sub_0", blocks=(), calls=()):
        self.start = start
        self.name = name
        self.symbol = SimpleNamespace(raw_name=name)
        self.basic_blocks = blocks
        self.call_sites = [SimpleNamespace(address=addr) for addr in calls]


class FakeView:
    def __init__(self):
        self.arch = SimpleNamespace(name="x86_64", address_size=8)
        self.functions = [
            FakeFunction(
                0x401000,
                name="main",
                blocks=[FakeBlock(0x401000, [2, 3], successors=())],
            )
        ]
        self.segments = [SimpleNamespace(start=0x401000, end=0x402000, data_length=16)]
        self._lengths = {0x401000: 2, 0x401002: 3}
        self._image = bytes(range(0x10, 0x15))
        self.reads = 0

    def get_function_at(self, offset):
        return next((function for function in self.functions if function.start == offset), None)

    def get_instruction_length(self, offset, arch=None):
        return self._lengths.get(offset, 0)

    def read(self, offset, length):
        self.reads += 1
        start = offset - 0x401000
        if 0 <= start < len(self._image):
            return self._image[start : start + length]
        return b"\x00" * length

    def get_callees(self, address, func=None):
        return []

    def get_symbols_of_type(self, symbol_type):
        return []

    def get_sections_at(self, offset):
        return []


def test_interface_exports_bn_offsets():
    interface = BinjaSmdaInterface(FakeView())
    assert interface.getArchitecture() == "intel"
    assert interface.getBitness() == 64
    assert interface.getFunctions() == [0x401000]
    assert interface.getBlocks(0x401000) == [[0x401000, 0x401002]]
    assert interface.getFunctionSymbols() == {0x401000: "main"}
    assert interface.getInstructionBytes(0x401000) == b"\x10\x11"
    assert interface.getInstructionBytes(0x401002) == b"\x12\x13\x14"
    assert interface.getBaseAddr() == 0x400000


def test_block_is_decoded_once_and_bytes_are_reused():
    view = FakeView()
    interface = BinjaSmdaInterface(view)
    interface.getBlocks(0x401000)
    interface.getCodeOutRefs(0x401000)
    interface.getInstructionBytes(0x401000)
    interface.getInstructionBytes(0x401002)
    assert view.reads == 1
