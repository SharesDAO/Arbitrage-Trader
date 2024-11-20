from enum import Enum


class PositionStatus(Enum):
    TRADABLE = 1
    PENDING_BUY = 2
    PENDING_SELL = 3
    PENDING_CANCEL = 4


# For each buy how many XCH you want to spend
BUY_VOLUME = 5
# Chia install folder
CHIA_PATH = "G:\\Program Files\\Chia\\resources\\app.asar.unpacked\\daemon\\chia.exe"
# Gas fee for  each transaction
CHIA_TX_FEE = 0
MAX_BUY_TIMES = 3  # Maximum number of repurchases for each stock
# Your DID HEX. You need to register it on the www.sharesdao.com before trading!
DID_HEX = 'a61489cbc7645829fc826606aba4ab5b09fdb2a69f40eb4b0bdae7a7dda7cf10'
# You Chia wallet fingerprint
WALLET_FINGERPRINT = 2701109320
# Sell all volume when profit is more than this
MIN_PROFIT = 0.01
# Repurchase the stock if the (last buy price - current price) / last buy price is less than this
DCA_PERCENTAGE = 0.05
# How much XCH you invested
INVESTED_XCH = 120
# Symbols you want to trade
TRADING_SYMBOLS = ["AAPL", "AMZN", "GOOGL", "MSFT", "NVDA", "TSLA", "META", "PYPL", "RDDT", "COIN", "GBTC", "AMD",
                   "MCD", ]
STOCKS = {
    "AAPL": {
        "buy_addr": "xch1u6679jwhyycxjmtahe7g89xmgpjuykw2j0322h740e7xdf72r43quh9suj",
        "sell_addr": "xch1hzz2ql4fpjz6g6qwl5tq3ws54xk3vkhmnzy58zz8sudcxzrsm00suzpvv0",
        "asset_id": "2216f8908f867313873416c0fe31487f453c7595f74e882957ffbdde520f231c"
    },
    "AMZN": {
        "buy_addr": "xch1j4mf9f5skh3cmvemw5vx0mkfzerf0drqth3h00zy2gkk7zpty0dslxd35w",
        "sell_addr": "xch1e20xaurr4d2pe89m6hpm3c0fjkeq3pf0un6lqsan5cdpy2shlm6qjw88sp",
        "asset_id": "e625e07b99503ece65d7162ce2b14b852887457b4c70973cb758cb8cb231c816"
    },
    "GOOGL": {
        "buy_addr": "xch1jx8kgqdf3yqcwpu7x9sdck2x3kuxwhpttll52l6w7mtu9fe8jj2shj9nrj",
        "sell_addr": "xch1jp9q9x0zzgvudmg6t0ntge7ukljjtgawlhjmcj2qkm9ctrjwdp8sen8sl5",
        "asset_id": "c1503a242359a8fc2fbc9a2b99bd78bcda259058aa693bf5c14df759726b7494"
    },
    "MSFT": {
        "buy_addr": "xch1euu0ge8pwghezmjhznsyzqavn9dckefpzr0y7u93az6y6wvk848s45fzex",
        "sell_addr": "xch168amzl0hk7cpgegvnstvxefusgvvcwrh48mvrkue5q0a6pe662wqafqhna",
        "asset_id": "f0bd9a482e274759306a43844b213511187adea86185fea835be34404400eb5b"
    },
    "NVDA": {
        "buy_addr": "xch1pjqlypx8pp2vp434j67945v7cy2vxn9psd05vd306cdj9wkdhv5qre60ay",
        "sell_addr": "xch1aqtnr7etqe56s05ydqv4xsun62jlaq0ydhvtp68n7rf0366m7sfsqlh8r4",
        "asset_id": "71c2f4adc8fe6e67924218e4dd2939074249e5895a11ed097aad4eb501cef317"
    },
    "TSLA": {
        "buy_addr": "xch12u0776v36yvpunukgr5ycspgl685qhdl6ya43gczl04mhflf6nlqutwfmu",
        "sell_addr": "xch18jyg49kzh3xpyrf7x6fzsmlyw0l3klhdt40m4um2lzr2luay5ujqwfzp92",
        "asset_id": "2244c79f659994df2c29f1874b06d02a059279c76948875d40d90fa1d7923fc0"
    },
    "META": {
        "buy_addr": "xch1w8vnfg7hr7qtf0uzwpwwc49vjmcv4hx5nlpsrjpml8u0hpsdrqtqz8k7ma",
        "sell_addr": "xch15na8llaerwwdqn0nslxafvyr6ls472vlgtwptmqg6zzsp7ct55xqm3qv96",
        "asset_id": "32420777e5ab258fa8ef642e8b24b4dade74f373dbce6831c9019e3654c8d586"
    },
    "PYPL": {
        "buy_addr": "xch1kx8spknfwqncp3keh64cexjl7lrp80e4aqx2d0hxl8473f9sv98q2fnr7d",
        "sell_addr": "xch1y3a0l5ccw9k83pmx0lj3twcqpm6f4pgfz485hq47zfkgye74e5csl2aedx",
        "asset_id": "ef799eb37ef42f113a011c34fad270f316827152b333dde4bd594b4ed7a7c140"
    },
    "RDDT": {
        "buy_addr": "xch1uyxr4sempfwlehcu88feftpm3rffemdv5hxmu7llpauv287pu9xqn5q3fn",
        "sell_addr": "xch1xryz09ack354gqh9pkqtk7yutlpmgs5tttcx7fd0hw6ernsqrrtsfefyf5",
        "asset_id": "9e136e62e48d1a58a395676867305a4bd7119facd9c7ec5f4ee2b1b7cb7fb9ca"
    },
    "COIN": {
        "buy_addr": "xch13sxtk5htysqg2dyqq9htdkw2vnn04g2a7m9yqx2x863jxjx53z8qgtxdej",
        "sell_addr": "xch1z7j3lpht65xre0zqs3qrajj5vd8hazvlcay7c9ewvwcf4tdjfwes9vcg7d",
        "asset_id": "86c79453698c7ac561c94ee1bb4f3daea066d8c5cd1c814636dd783e68685d4a"
    },
    "YUM": {
        "buy_addr": "xch1lfemdzeaj6tx0pvedr4mm5g0fx06kfswe8zrjuw09dsgs0fq0gmstwpyu5",
        "sell_addr": "xch13weyaa46kjkgwv9cn90ft2dq55gx4ffk3dkmmvaxsc2ke4svceus4wx04v",
        "asset_id": "684ee9c65f7447e0440e9c610d274f2d263311b1412a769d9f373a8e6a3de78e"
    },
    "GME": {
        "buy_addr": "xch134pnmquc7fqny4ddv8qkfk69sj4hmjtupy89z9nrnk7ky4ev44uqyxv3yw",
        "sell_addr": "xch14afry25mwel5uzstlclj85y3kd75fax2yrhq9j00ze87nr5cydlqy5e3rq",
        "asset_id": "b7876fa0749ddb83b682d7ba9827d620ccd1cce82cb1d0d1a40aec84415f0ba0"
    },
    "PFE": {
        "buy_addr": "xch16vlfkuwzhcx0v3nhk9h8qr7vfwejywmu3ffcttpf9fhq2m2aplzqlhkw9r",
        "sell_addr": "xch13tml87usrvse6dphqln6xventrqvh8nadp7q88y9hc95ef3k5y0qg9670e",
        "asset_id": "a1595866a344adc93356d4b7842493051d46a32d06f8fe18f0ab62c82d8c22bb"
    },
    "SLX": {
        "buy_addr": "xch17z5nhrwexftwj7vvv87y9jaj5nl2u5w2l9az72j9dkuq0u4gc32s0nk0kv",
        "sell_addr": "xch1xmk7uahk2wft3fw3suds0qpjsayx9ycsj0a0qar3eeaexy4az4qqg8t8ul",
        "asset_id": "cb3a279e565463cfeb1740aea4d10d7ef579a40966018bdb78f890d2fd84da0a"
    },
    "RIOT": {
        "buy_addr": "xch1p9zl9heq2pp5ktq30ghrl5knrfh33d7ckrcv07mdvvpq0zpzez9qfhzw66",
        "sell_addr": "xch1yw8x2wunmq24w25cd0rea4qdple7dj8wayhf46uesqfhkrtc7jgsag5fq2",
        "asset_id": "f0895ec526ca1dee646cad563be0ed68e6ae0a316bd00385957da1078e6da00f"
    },
    "MCD": {
        "buy_addr": "xch1q7v2g0d6xpalu7j9efee6xce4j8j77n30eynnul2a7d6nutq4jnqdtu73w",
        "sell_addr": "xch16l22zayhsvzd3nd5uxj2hz9htrcueg4ykwnk8wv98x5lyplqla9qa4ys42",
        "asset_id": "e8c6a1817e8e4ea5f0f2aa5adf087dc8129f62a8a9cb6b0efccfeb11307f8888"
    },
    "DIS": {
        "buy_addr": "xch1j2d56a2qj08vr3hchvwtfpq799f0s88qxc58kkufz0g9svgukl3s5vzfck",
        "sell_addr": "xch1nph6j5fzq5nj8h8fpwrl5xane3wcm54879gyjc6xsv0zvscqmxxs9l9g2q",
        "asset_id": "ffa1ed47e712ec4150b1d954de8df1f1c74fcbb1314938ed29ab8626a5bbc471"
    },
    "SPY": {
        "buy_addr": "xch1n3gd7tjwf25js65e67nqwwf9cphlusd9ldkl9zsk9xezehte7c0qftkelg",
        "sell_addr": "xch1uana5s980rpf3zewyagvhduup2unjt020nc0hhe3efpcerm3q7jq66w4e5",
        "asset_id": "79d2f5f7c6008bbd19cb76c0c7fc27eb71d88f0b78f320a7f87ca63a90821b35"
    },
    "NFLX": {
        "buy_addr": "xch18qrnvvwj5zthrpr2jaqlxxvxylytpwurkv8dxxa4q5amgxfky5lqf3tpxg",
        "sell_addr": "xch180a2adltf4jacvtfd34302l2pm9pudvmu7ldrswjlagnkjdu6n6s3rktm5",
        "asset_id": "8f3d32eb8f6f971f69118aaafe7cb43e54af57c650d0342063ea1fc649358d8f"
    },
    "GBTC": {
        "buy_addr": "xch1zc23utj6xt7g7qd4r4nnrzz5s6t5fjp7u9sjwjz6c20cth66sh7qf878pa",
        "sell_addr": "xch1mm6ht5yayvq72dtekev2sg2s9ak3jwnwcku4z9xsu0dvpjca4yls0dsr60",
        "asset_id": "cc68a95698ff89ca8da239041f7dc79bdaa17f71a043eedb0664a173752f6b0a"
    },
    "AMD": {
        "buy_addr": "xch1pf9jkvng04rl0xcwjv8g4zhal4xfj9smupa30sxm0kg3a5jmzpmquy93mt",
        "sell_addr": "xch1s37jwp77edcc3dkwtkvg89xrj7yrrjqw3q2kreka4ja4xw2kstpswrzp6f",
        "asset_id": "d84b8940f0312c6bd2c25365e5bce7fe9377778f67d2d29b1ba5c19d6c0ab6b0"
    },
    "PHO": {
        "buy_addr": "xch1gy87clvts2klms6wynsz33mkqm6kwcas8yfktxr7aeg4xe2gq8gqlnzw79",
        "sell_addr": "xch1ejwvvym50rrsjjfugg3sha60cxxtx6ld5pv6jn9r2v9mgqwmfkjspw85xm",
        "asset_id": "48dc34becb78a2b605a9621314f1b32e823c604790c5ec707e053d19d04814f2"
    },
    "SQ": {
        "buy_addr": "xch1l65az2ycfvyenhcnzjjd2ncwej03p60l7uqmaluapgrfpdj26gesv6quy0",
        "sell_addr": "xch1cm287cktwgy377v7xl03dgd2ayjaxl8w9pnh4udmsx7uff4xghus6uargn",
        "asset_id": "529e9298881e2ef7676ba281a227ec1b842253f1f4694aeb7d8406a4c969fe9b"
    },
    "ARM": {
        "buy_addr": "xch1hy537hsejkdrdzqqp8asfyyhs5k3nq9cu4u3ca6gm42grw509w3qa8237y",
        "sell_addr": "xch12ea2ll45tsm7rsj2n7syjnr7qqy29psktu5md75t4p4qfk9xs9vqpgt7e9",
        "asset_id": "9afee1d18bdf771303364a2935b8232c6b0a7bfeeaf6a2a82e6fbc6ac16f461c"
    },
    "WEAT": {
        "buy_addr": "xch10dyglswh8wvm9z24vfemhxlu0qlnpaphjfsphhg0td43mltmdvkq8cmrnf",
        "sell_addr": "xch15jfe23khvjtwn09hksxcayd8t0rwynrp6nu3ymts5azpmd6xvcgqq3h34d",
        "asset_id": "4a760e9ac3a928f036fcdf20c52ef6c994fdbd72769df65f4cda58ba1a7abe4c"
    },
    "NLY": {
        "buy_addr": "xch1r4ww9lg9ftqfzktsdkz0lfyljqk3ykyg3g58gyatj8w3xnr26krqy09444",
        "sell_addr": "xch14vqm7e7as0khz27jwdvln3femfanmh70ukh2kqlgt79kfmv33n5qvxn5ns",
        "asset_id": "9afee1d18bdf771303364a2935b8232c6b0a7bfeeaf6a2a82e6fbc6ac16f461c"
    },
    "WOOD": {
        "buy_addr": "xch1kw37esdnnlrr7cg2fmwpezf7349wftevtpryqp4n3szusl6slq3sfx79tp",
        "sell_addr": "xch1pqxq53q9m5fenlpwhm60wq0haymlxsy3905udc0xncjszcv5wzdqthlwa0",
        "asset_id": "245671b09acb3ef30aec6cc59880a6b27e8f06bc1b29ac51ada168673950f7b2"
    }
}
