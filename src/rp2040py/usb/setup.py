from rp2040py.usb.interfaces import (
    DataDirection,
    DescriptorType,
    ISetupPacketParams,
    SetupRecipient,
    SetupRequest,
    SetupType,
)

__all__ = (
    "create_setup_packet",
    "get_descriptor_packet",
    "set_device_address_packet",
    "set_device_configuration_packet",
)


def create_setup_packet(params: ISetupPacketParams) -> bytearray:
    setup_packet = bytearray(8)
    setup_packet[0] = (params.data_direction << 7) | (params.type << 5) | params.recipient
    setup_packet[1] = params.b_request
    setup_packet[2] = params.w_value & 0xFF
    setup_packet[3] = (params.w_value >> 8) & 0xFF
    setup_packet[4] = params.w_index & 0xFF
    setup_packet[5] = (params.w_index >> 8) & 0xFF
    setup_packet[6] = params.w_length & 0xFF
    setup_packet[7] = (params.w_length >> 8) & 0xFF
    return setup_packet


def set_device_address_packet(address: int) -> bytearray:
    return create_setup_packet(
        ISetupPacketParams(
            data_direction=DataDirection.HOST_TO_DEVICE,
            type=SetupType.STANDARD,
            recipient=SetupRecipient.DEVICE,
            b_request=SetupRequest.SET_ADDRESS,
            w_value=address,
            w_index=0,
            w_length=0,
        )
    )


def get_descriptor_packet(type: DescriptorType, length: int, index: int = 0) -> bytearray:
    return create_setup_packet(
        ISetupPacketParams(
            data_direction=DataDirection.DEVICE_TO_HOST,
            type=SetupType.STANDARD,
            recipient=SetupRecipient.DEVICE,
            b_request=SetupRequest.GET_DESCRIPTOR,
            w_value=type << 8,
            w_index=index,
            w_length=length,
        )
    )


def set_device_configuration_packet(configuration_number: int) -> bytearray:
    return create_setup_packet(
        ISetupPacketParams(
            data_direction=DataDirection.HOST_TO_DEVICE,
            type=SetupType.STANDARD,
            recipient=SetupRecipient.DEVICE,
            b_request=SetupRequest.SET_DEVICE_CONFIGURATION,
            w_value=configuration_number,
            w_index=0,
            w_length=0,
        )
    )
