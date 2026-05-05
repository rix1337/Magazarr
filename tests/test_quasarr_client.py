from magazarr.quasarr_client import QuasarrClient, parse_search_results


def test_parse_quasarr_search_results():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss>
      <channel>
        <item>
          <title>Linux Format May 2026</title>
          <link>http://quasarr/download/?payload=abc</link>
          <comments>source</comments>
          <pubDate>Tue, 05 May 2026 10:00:00 +0000</pubDate>
          <enclosure url="http://quasarr/download/?payload=abc" length="1234" type="application/x-nzb" />
        </item>
      </channel>
    </rss>
    """
    results = parse_search_results(xml)
    assert len(results) == 1
    assert results[0].title == "Linux Format May 2026"
    assert results[0].size_bytes == 1234


def test_search_fetches_all_pages():
    class Response:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    class Session:
        def __init__(self):
            self.headers = {}
            self.offsets = []

        def get(self, url, params, timeout):
            self.offsets.append(params["offset"])
            offset = params["offset"]
            titles = {
                0: ["Linux Format May 2026", "Linux Format April 2026"],
                2: ["Linux Format March 2026", "Linux Format February 2026"],
                4: ["Linux Format January 2026"],
            }[offset]
            return Response(_xml(titles))

    client = QuasarrClient("http://quasarr", "key")
    session = Session()
    client.session = session

    results = client.search("Linux Format", "7000", page_limit=2)

    assert session.offsets == [0, 2, 4]
    assert [item.title for item in results] == [
        "Linux Format May 2026",
        "Linux Format April 2026",
        "Linux Format March 2026",
        "Linux Format February 2026",
        "Linux Format January 2026",
    ]


def _xml(titles):
    items = "\n".join(
        f"""
        <item>
          <title>{title}</title>
          <link>http://quasarr/download/?payload={idx}</link>
          <pubDate>Tue, 05 May 2026 10:00:00 +0000</pubDate>
          <enclosure url="http://quasarr/download/?payload={idx}" length="1234" type="application/x-nzb" />
        </item>
        """
        for idx, title in enumerate(titles)
    )
    return f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss><channel>{items}</channel></rss>"
