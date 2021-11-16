import datetime
import json
import logging
import math
import re
from urllib.parse import urlparse

import dukpy
import scrapy
from scrapy.loader import ItemLoader

from wildsearch_crawler.items import WildsearchCrawlerItemWildberries

from .base_spider import BaseSpider

logger = logging.getLogger(__name__)


class WildberriesSpider(BaseSpider):
    name = "wb"

    def start_requests(self):
        category_urls = getattr(self, 'category_url', None)

        if category_urls is not None:
            for category_url in category_urls.split(','):
                yield scrapy.Request(category_url, self.parse_category)

            return

        good_urls = getattr(self, 'good_url', None)

        if good_urls is not None:
            for good_url in good_urls.split(','):
                yield scrapy.Request(good_url, self.parse_good)

            return

        # start crawl from sitemap or main menu
        if getattr(self, 'use_sitemap', False):
            yield scrapy.Request("https://www.wildberries.ru/services/karta-sayta", self.parse_sitemap)
        else:
            yield scrapy.Request('https://www.wildberries.ru/menu/getrendered?lang=ru&burger=true', self.parse_main_menu, headers={'x-requested-with': 'XMLHttpRequest'})

    def parse(self, response):
        pass

    def parse_main_menu(self, response):
        for url in response.css('a::attr(href)'):
            yield response.follow(url, self.parse_category)

    def parse_sitemap(self, response):
        for url in response.css('#sitemap a::attr(href)'):
            yield response.follow(url, self.parse_category)

    def parse_category(self, response):
        def clear_url_params(url):
            return url.split('?')[0]

        def generate_api_url(response, page):
            path = response.css('#catalog::attr(data-xcatalog-path)').get()
            query = response.css('#catalog::attr(data-xcatalog-query)').get()

            return 'https://wbxcatalog-ru.wildberries.ru/nm-2-card/catalog?' + query + '&page=' + str(page)

        category_url = clear_url_params(response.url)
        category_name = response.css('h1::text').get()

        if response.css('#catalog::attr(data-xcatalog-path)').get() is not None:
            wb_category_position = 0

            items_on_page = len(response.css('.j-card-item'))
            total_goods = int(response.css('#catalog::attr(data-xcatalog-total)').get())

            pages = math.ceil(total_goods / items_on_page)

            for page in range(1, pages + 1):
                yield response.follow(generate_api_url(response, page), callback=self.parse_category_page_json, meta={
                    'current_position': wb_category_position,
                    'category_url': category_url,
                    'category_name': category_name,
                })

                wb_category_position += items_on_page
        else:
            wb_category_position = int(response.meta['current_position']) if 'current_position' in response.meta else 1

            allow_dupes = getattr(self, 'allow_dupes', False)
            skip_details = getattr(self, 'skip_details', False)

            # follow links to goods pages
            for item in response.css('.catalog-content .j-card-item'):
                good_url = item.css('a.ref_goods_n_p::attr(href)')

                if skip_details:
                    # ItemLoader выключен в угоду скорости

                    yield {
                        'wb_id': re.findall(r'\/catalog\/(\d{1,20})\/detail\.aspx', clear_url_params(good_url.get()))[
                            0],
                        'product_name': item.css('.goods-name::text').get(),
                        'wb_reviews_count': item.css('.dtList-comments-count::text').get(),
                        'wb_price': item.css('.lower-price::text').get().replace(u'\u20bd', '').replace(u'\u00a0', ''),
                        'parse_date': datetime.datetime.now().isoformat(" "),
                        'marketplace': 'wildberries',
                        'product_url': clear_url_params(good_url.get()),
                        'wb_category_url': category_url,
                        'wb_category_name': category_name,
                        'wb_category_position': wb_category_position,
                        'wb_brand_name': item.css('.brand-name::text').get().strip()
                    }
                else:
                    yield response.follow(clear_url_params(good_url.get()), self.parse_good, dont_filter=allow_dupes,
                                          meta={
                                              'current_position': wb_category_position,
                                              'category_url': category_url,
                                              'category_name': category_name
                                          })

                wb_category_position += 1

            # follow pagination
            for a in response.css('.pager-bottom a.pagination-next'):
                yield response.follow(a, callback=self.parse_category, meta={'current_position': wb_category_position})

    def parse_category_page_json(self, response):
        def generate_good_url(id, base_url):
            base_url_parsed = urlparse(base_url)

            return base_url_parsed.scheme + '://www.wildberries.ru' + '/catalog/' + str(id) + '/detail.aspx'

        skip_details = getattr(self, 'skip_details', False)
        allow_dupes = getattr(self, 'allow_dupes', False)

        category_data = json.loads(response.text)

        wb_category_position = int(response.meta['current_position']) if 'current_position' in response.meta else 0
        wb_category_url = response.meta['category_url']
        wb_category_name = response.meta['category_name']

        for item in category_data['data']['products']:
            wb_category_position += 1

            if skip_details:
                yield {
                    'wb_id': item['id'],
                    'product_name': item['name'],
                    'wb_reviews_count': item['feedbackCount'],
                    'wb_price': item['salePrice'],
                    'parse_date': datetime.datetime.now().isoformat(" "),
                    'marketplace': 'wildberries',
                    'product_url': generate_good_url(item['id'], response.url),
                    'wb_category_url': wb_category_url,
                    'wb_category_name': wb_category_name,
                    'wb_category_position': wb_category_position,
                    'wb_brand_name': item['brand']
                }
            else:
                yield response.follow(generate_good_url(item['id'], response.url), self.parse_good, dont_filter=allow_dupes, meta={
                    'current_position': wb_category_position,
                    'category_url': wb_category_url,
                    'category_name': wb_category_name
                })

    def parse_good(self, response):
        def clear_url_params(url):
            return url.split('?')[0]

        def generate_reviews_link(base_url, sort='Asc'):
            # at first it is like https://www.wildberries.ru/catalog/8685970/detail.aspx
            # must be like https://www.wildberries.ru/catalog/8685970/otzyvy?field=Date&order=Asc
            link_param = response.css('#Comments a.show-more::attr(data-link)').get()

            return re.sub('detail\.aspx.*$', f'otzyvy?field=Date&order={sort}&link={link_param}', base_url)

        def add_netloc_to_url(url, base_url):
            url_parsed = urlparse(url)

            if url_parsed.scheme == '' and url_parsed.netloc == '':
                base_url_parsed = urlparse(base_url)

                url = base_url_parsed.scheme + '://' + base_url_parsed.netloc + url

            return url

        skip_images = getattr(self, 'skip_images', False)
        skip_variants = getattr(self, 'skip_variants', False)
        allow_dupes = getattr(self, 'allow_dupes', False)

        current_good_item = WildsearchCrawlerItemWildberries()
        parent_item = response.meta['parent_item'] if 'parent_item' in response.meta else None

        loader = ItemLoader(item=current_good_item, response=response)

        # category position stats
        wb_category_url = response.meta['category_url'] if 'category_url' in response.meta else None
        wb_category_name = response.meta['category_name'] if 'category_name' in response.meta else None
        wb_category_position = response.meta['current_position'] if 'current_position' in response.meta else None

        canonical_url = response.css('link[rel=canonical]::attr(href)').get()
        canonical_url = add_netloc_to_url(canonical_url, response.url)

        if canonical_url != response.url:
            yield response.follow(clear_url_params(canonical_url), self.parse_good, dont_filter=allow_dupes,  meta={
                'current_position': wb_category_position,
                'category_url': wb_category_url
            })

            return

        # scraping brand and manufacturer countries
        wb_brand_country = ''
        wb_manufacture_country = ''

        for param in (response.css('.params .pp')):
            param_name = param.css('span:nth-of-type(1) b::text').get()
            param_value = param.css('span:nth-of-type(2)::text').get()

            if u'Страна бренда' == param_name:
                wb_brand_country = param_value

            if u'Страна производитель' == param_name:
                wb_manufacture_country = param_value

        wb_id = response.css('div.article span::text').get()

        # fill css selectors fields
        loader.add_css('product_name', '.brand-and-name .name::text')
        loader.add_css('wb_reviews_count', '.count-review i::text')
        loader.add_css('wb_price', '.final-cost::text')
        loader.add_css('wb_rating', '.product-rating span::text')
        loader.add_css('wb_id', 'div.article span::text')

        # fill non-css values
        loader.add_value('wb_id', wb_id)
        loader.add_value('parse_date', datetime.datetime.now().isoformat(" "))
        loader.add_value('marketplace', 'wildberries')
        loader.add_value('product_url', response.url)
        loader.add_value('wb_brand_name', response.css('.brand-and-name .brand::text').get())
        loader.add_value('wb_brand_url', response.css('.brand-logo a::attr(href)').get())
        loader.add_value('wb_brand_logo', response.css('.brand-logo img::attr(src)').get())
        loader.add_value('wb_brand_country', wb_brand_country)
        loader.add_value('wb_manufacture_country', wb_manufacture_country)
        loader.add_value('wb_category_url', wb_category_url)
        loader.add_value('wb_category_name', wb_category_name)
        loader.add_value('wb_category_position', wb_category_position)

        # create list of images
        if skip_images is False:
            image_urls = []

            for tm in (response.css('.pv-carousel .swiper-slide img::attr(src)')):
                image_urls.append(tm.get().strip().replace('tm', 'big'))

            loader.add_value('image_urls', image_urls)

        # create list of features
        features = {}

        for feature in response.css('.params .pp'):
            features[feature.css('span:nth-of-type(1) b::text').get().strip()] = feature.css('span:nth-of-type(2)::text').get().strip()

        loader.add_value('features', features)

        # get purchase count from inline JavaScript block with data
        products_data_js_v1 = response.xpath('//script[contains(., "wb.product.DomReady.init")]/text()').get()

        if products_data_js_v1 is not None and str(products_data_js_v1) != '':
            loader.add_value('wb_purchases_count', self.parse_purchases_count_v1(products_data_js_v1, wb_id))

        products_data_js_v2 = response.xpath('//script[contains(., "wb.spa.init")]/text()').get()

        if products_data_js_v2 is not None and str(products_data_js_v2) != '':
            loader.add_value('wb_purchases_count', self.parse_purchases_count_v2(products_data_js_v2, wb_id))

        if parent_item is not None:
            loader.add_value('wb_parent_id', parent_item.get('wb_id', ''))

        # get reviews dates
        yield response.follow(generate_reviews_link(response.url, 'Asc'), callback=self.parse_good_first_review_date, errback=self.parse_good_errback, meta={'loader': loader}, headers={'x-requested-with': 'XMLHttpRequest'})

        # follow goods variants only if we scrape parent item
        if skip_variants is False and parent_item is None:
            for variant in (response.css('.options ul li a::attr(href)')):
                yield response.follow(clear_url_params(variant.get()), callback=self.parse_good, meta={
                    'parent_item': current_good_item
                })

    def parse_purchases_count_v1(self, products_data_js, wb_id):
        products_data_js = re.sub('\n', '', products_data_js)
        products_data_js = re.sub(r'\s{2,}', '', products_data_js)

        products_init = re.findall(r'wb\.product\.DomReady\.init\(({.*?})\);', products_data_js)[0]

        if products_init is not None and str(products_init) != '':
            interpreter = dukpy.JSInterpreter()
            evaled_data = interpreter.evaljs(f'init={products_init};init.data;')

            if evaled_data is not None and 'nomenclatures' in evaled_data.keys():
                for sku_id, data in evaled_data['nomenclatures'].items():
                    if sku_id == wb_id:
                        return data['ordersCount']

    def parse_purchases_count_v2(self, products_data_js, wb_id):
        products_data_js = re.sub('\n', '', products_data_js)
        products_data_js = re.sub(r'\s{2,}', '', products_data_js)

        products_data_js = re.sub('routes: routes,', '', products_data_js)
        products_data_js = re.sub('routesDictionary: routesDictionary,', '', products_data_js)

        products_init = re.findall(r'wb\.spa\.init\(({.*?})\);', products_data_js)[0]

        if products_init is not None and str(products_init) != '':
            interpreter = dukpy.JSInterpreter()
            evaled_data = interpreter.evaljs(f'init={products_init};init.router;')

            if 'ssrModel' in evaled_data.keys():
                ssrModel = evaled_data['ssrModel']

                if 'selectedNomenclature' in ssrModel.keys():
                    selectedNomenclature = ssrModel['selectedNomenclature']

                    if 'ordersCount' in selectedNomenclature:
                        return selectedNomenclature['ordersCount']

    def parse_good_first_review_date(self, response):
        if len(response.css('.comment')) > 0:
            response.meta['loader'].add_value('wb_first_review_date', response.css('.comment')[0].css('.time::attr(content)').get())

        yield response.meta['loader'].load_item()

    def parse_good_errback(self, response):
        yield response.meta['loader'].load_item()
