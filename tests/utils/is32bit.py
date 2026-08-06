import struct

IS32BIT = struct.calcsize("P") * 8 <= 32
