/* © Copyright CERN, 2026. BSD-2-Clause.
 * Registry lookup over the plain REST API — no JSON library needed for the
 * flat lookup document. */
package ch.quasarnova.hypernova;

import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class Registry {

    public static final class Coordinates {
        public String address;      // opc.udp://host:port
        public long publisherId;
        public int writerGroupId;
        public int dataSetWriterId;
        public final List<String> fieldNames = new ArrayList<>();
    }

    private static final Pattern ADDRESS = Pattern.compile("\"address\"\\s*:\\s*\"([^\"]+)\"");
    private static final Pattern PUBLISHER = Pattern.compile("\"publisherId\"\\s*:\\s*(\\d+)");
    private static final Pattern GROUP = Pattern.compile("\"writerGroupId\"\\s*:\\s*(\\d+)");
    private static final Pattern WRITER = Pattern.compile("\"dataSetWriterId\"\\s*:\\s*(\\d+)");
    private static final Pattern FIELD = Pattern.compile("\\{\\s*\"name\"\\s*:\\s*\"([^\"]+)\"");

    /** GET {registry}/api/lookup/{name}[?network=...]; comma-separated
     *  registry URLs fail over in order (primary/secondary). */
    public static Coordinates lookup(String registries, String name, String network)
            throws IOException {
        IOException lastError = null;
        for (String registry : registries.split(",")) {
            String base = registry.trim().replaceAll("/+$", "");
            if (base.isEmpty()) continue;
            StringBuilder url = new StringBuilder(base).append("/api/lookup/").append(name);
            if (network != null) {
                url.append("?network=").append(URLEncoder.encode(network, "UTF-8"));
            }
            try {
                return parse(fetch(url.toString()));
            } catch (IOException | RuntimeException error) {
                lastError = error instanceof IOException
                        ? (IOException) error
                        : new IOException("registry answer unparseable: " + error.getMessage());
            }
        }
        throw lastError != null ? lastError : new IOException("no registry URL given");
    }

    private static String fetch(String url) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
        connection.setConnectTimeout(5000);
        connection.setReadTimeout(5000);
        int status = connection.getResponseCode();
        if (status != 200) {
            throw new IOException("registry " + url + " -> HTTP " + status);
        }
        try (InputStream in = connection.getInputStream()) {
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    static Coordinates parse(String json) throws IOException {
        Coordinates coordinates = new Coordinates();
        coordinates.address = firstGroup(ADDRESS, json, "address");
        coordinates.publisherId = Long.parseUnsignedLong(firstGroup(PUBLISHER, json, "publisherId"));
        coordinates.writerGroupId = Integer.parseInt(firstGroup(GROUP, json, "writerGroupId"));
        coordinates.dataSetWriterId = Integer.parseInt(firstGroup(WRITER, json, "dataSetWriterId"));
        int fieldsStart = json.indexOf("\"fields\"");
        if (fieldsStart >= 0) {
            Matcher fields = FIELD.matcher(json.substring(fieldsStart));
            while (fields.find()) coordinates.fieldNames.add(fields.group(1));
        }
        return coordinates;
    }

    private static String firstGroup(Pattern pattern, String json, String what) throws IOException {
        Matcher matcher = pattern.matcher(json);
        if (!matcher.find()) throw new IOException("registry answer lacks " + what);
        return matcher.group(1);
    }

    private Registry() { }
}
