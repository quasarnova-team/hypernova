/* Golden-vector generator: encodes a set of NetworkMessages with supernova's
 * C++ PubSubWire codec (the engine quasar/supernova servers ship) and prints
 * them as JSON hex. The Python codec must decode these exactly and re-encode
 * them byte-identically — cross-implementation parity, proven not assumed.
 *
 * Build (supernova checkout required):
 *   g++ -std=c++11 -I <supernova>/PubSub/include gen_golden.cpp \
 *       <supernova>/PubSub/src/PubSubWire.cpp -o gen_golden
 */

#include <PubSubWire.h>

#include <cstdio>
#include <string>
#include <vector>

using namespace PubSub;

static void emit(const char* name, const NetworkMessage& message, bool first)
{
    std::vector<uint8_t> wire = encodeNetworkMessage(message);
    std::string hex;
    char byte[3];
    for (size_t i = 0; i < wire.size(); i++)
    {
        std::snprintf(byte, sizeof byte, "%02x", wire[i]);
        hex += byte;
    }
    std::printf("%s  {\"name\": \"%s\", \"hex\": \"%s\"}", first ? "" : ",\n", name, hex.c_str());
}

int main()
{
    std::printf("[\n");

    {
        NetworkMessage m;
        m.publisherIdType = PublisherIdUInt16;
        m.publisherId = 2234;
        m.writerGroupId = 100;
        m.groupSequenceNumber = 1;
        DataSetMessage d;
        d.dataSetWriterId = 62541;
        d.sequenceNumberEnabled = true;
        d.sequenceNumber = 1;
        d.fields.push_back(WireValue::makeSigned(TypeInt32, 7));
        m.messages.push_back(d);
        emit("single_int32", m, true);
    }

    {
        NetworkMessage m;
        m.publisherIdType = PublisherIdUInt16;
        m.publisherId = 42;
        m.writerGroupId = 7;
        m.groupSequenceNumber = 3;
        DataSetMessage d;
        d.dataSetWriterId = 1;
        d.sequenceNumberEnabled = true;
        d.sequenceNumber = 99;
        d.fields.push_back(WireValue::makeNull());
        d.fields.push_back(WireValue::makeBoolean(true));
        d.fields.push_back(WireValue::makeSigned(TypeSByte, -5));
        d.fields.push_back(WireValue::makeUnsigned(TypeByte, 200));
        d.fields.push_back(WireValue::makeSigned(TypeInt16, -30000));
        d.fields.push_back(WireValue::makeUnsigned(TypeUInt16, 60000));
        d.fields.push_back(WireValue::makeSigned(TypeInt32, -2000000000));
        d.fields.push_back(WireValue::makeUnsigned(TypeUInt32, 4000000000u));
        d.fields.push_back(WireValue::makeSigned(TypeInt64, -9000000000000000000LL));
        d.fields.push_back(WireValue::makeUnsigned(TypeUInt64, 18000000000000000000ULL));
        d.fields.push_back(WireValue::makeFloat(3.5f));
        d.fields.push_back(WireValue::makeDouble(-2.25e-10));
        d.fields.push_back(WireValue::makeString("supernova"));
        d.fields.push_back(WireValue::makeString(""));
        d.fields.push_back(WireValue::makeDateTime(133774531200000000LL));
        m.messages.push_back(d);
        emit("all_scalars", m, false);
    }

    {
        NetworkMessage m;
        m.publisherIdType = PublisherIdByte;
        m.publisherId = 9;
        m.writerGroupId = 300;
        m.groupSequenceNumber = 77;
        for (int w = 0; w < 3; w++)
        {
            DataSetMessage d;
            d.dataSetWriterId = static_cast<uint16_t>(1000 + w);
            d.sequenceNumberEnabled = true;
            d.sequenceNumber = static_cast<uint16_t>(w);
            d.fields.push_back(WireValue::makeSigned(TypeInt32, w * 11));
            d.fields.push_back(WireValue::makeString(std::string(static_cast<size_t>(w + 1), 'x')));
            m.messages.push_back(d);
        }
        emit("three_writers_byte_pid", m, false);
    }

    {
        NetworkMessage m;
        m.publisherIdType = PublisherIdUInt64;
        m.publisherId = 12345678901234ULL;
        m.writerGroupId = 65535;
        m.groupSequenceNumber = 65535;
        DataSetMessage d;
        d.dataSetWriterId = 5;
        d.fields.push_back(WireValue::makeDouble(21.75));
        d.fields.push_back(WireValue::makeString("boundary \xE2\x9C\x93 utf8"));
        m.messages.push_back(d);
        emit("uint64_pid_utf8", m, false);
    }

    std::printf("\n]\n");
    return 0;
}
