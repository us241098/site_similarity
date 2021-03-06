import argparse
import json
import os
import logging
import random
import time

import redis
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

_DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'all_data')
_LOGGER = logging.getLogger('dataprep.alexa_scrapper')


class ScrapeAlexa:
    def __init__(self, target_dir=None):
        self.target_dir = target_dir if target_dir else _DATA_PATH
        os.makedirs(self.target_dir, exist_ok=True)

    def _is_site_already_checked(self):
        return f'{self.target_site}.html' in os.listdir(self.target_dir)

    @staticmethod
    def _get_alexa_audience_metrics(element):
        """ Returns data about audience overlap like:
        similar sites, overlap score, alexa rank
        :param element: BeautifulSoup object
        """

        similar_sites_by_audience_overlap = element.find('div', {'id': 'card_mini_audience'})
        audience_overlap = element.find('div', {'id': 'card_overlap'})

        if similar_sites_by_audience_overlap:
            resources = similar_sites_by_audience_overlap
        elif audience_overlap:
            resources = audience_overlap
        else:
            return

        similar_sites = [el['href'] for el in resources.find_all('a', {'class': 'truncation'})]
        overlap_score = [el.text for el in resources.find_all('span', {'class': 'truncation'})]

        sites_overlap_score = overlap_score[::2]
        alexa_score = overlap_score[1::2]

        # TODO Add implementation on how to handle when where isn't 'referral_sites'

        try:
            parsed_result = [{'url': site.replace("/siteinfo/", ''),
                              'overlap_score': float(ov_score) if ov_score and ov_score.strip() != '-' else None,
                              'alexa_rank': float(al_score.replace(',', '')) if al_score and al_score.strip() != '-' else None}
                             for site, ov_score, al_score in zip(similar_sites, sites_overlap_score, alexa_score)]
        except ValueError as e:
            _LOGGER.error(f'{similar_sites}')

        return parsed_result

    def scrape_alexa_site_info(self, target_site):
        self.target_site = target_site

        if self._is_site_already_checked():
            _LOGGER.info(f"This site '{self.target_site}' has already being processed")
            with open(f"{self.target_dir}/{self.target_site}.html") as f:
                response = f.read()

            element = BeautifulSoup(response, 'lxml')

        else:
            time.sleep(random.randint(1, 10))
            try:
                response = requests.get(f"https://www.alexa.com/siteinfo/{self.target_site}")
            except ConnectionError as e:
                _LOGGER.error(f"ConnectionError occurred when scrapping site: {self.target_site}. With error: {repr(e)}")
                time.sleep(20)
                response = requests.get(f"https://www.alexa.com/siteinfo/{self.target_site}")
            except Exception as e:
                _LOGGER.error(f"BaseError occurred when scrapping site: {self.target_site}. With error: {repr(e)}")
                time.sleep(20)
                response = requests.get(f"https://www.alexa.com/siteinfo/{self.target_site}")

            if "Alexa is temporarily unavailable.We're working hard to resolve the issue — please try again later" in response.text:
                time.sleep(20)
                response = requests.get(f"https://www.alexa.com/siteinfo/{self.target_site}")

            with open(os.path.join(self.target_dir, f'{self.target_site}.html'), 'w') as f:
                f.write(response.text)
            element = BeautifulSoup(response.text, 'lxml')

        countries = element.find_all('div', {'id': 'countryName'})
        percentages = element.find_all('div', {'id': 'countryPercent'})

        res = dict()
        res['site'] = self.target_site
        score = ScrapeAlexa._get_alexa_audience_metrics(element)
        if not score:
            _LOGGER.info(f"No similar_sites_by_audience_overlap or audience_overlap found for {self.target_site}")
        res['score'] = score
        if countries:
            res['audience_geography'] = [{'country': country.text.split("\xa0")[-1], 'percent': float(percent.text[:-1])}
                                         for country, percent in zip(countries, percentages)]
        else:
            res['audience_geography'] = []

        return res


def scrapping(data, target_dir=None, output_file=None):
    result = {}
    alexa_scrapper = ScrapeAlexa(target_dir)
    r = redis.Redis(host='localhost', port=6379, db=0)

    for index, child_sites in enumerate(tqdm(data.values())):
        if index % 100 == 0 and output_file:
            print(f"Save data at index {index} ...")
            with open(output_file, 'w') as f:
                json.dump(result, f, indent=4)
        for child_site in child_sites.values():
            result[child_site['site']] = {}
            for overlap_site in child_site.get('score', []):
                if r.exists(overlap_site['url']):
                    # load from redis
                    result[child_site['site']][overlap_site['url']] = json.loads(r.get(overlap_site['url']))
                else:
                    alexa_result = alexa_scrapper.scrape_alexa_site_info(overlap_site['url'])
                    # save in redis
                    r.set(overlap_site['url'], json.dumps(alexa_result))
                    result[child_site['site']][overlap_site['url']] = alexa_result
    return result


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument('--target_site', default='bradva.bg')

    args = parser.parse_args()

    result = ScrapeAlexa(args.target_site).scrape_alexa_site_info()

    with open(os.path.join(_DATA_PATH, f'{args.target_site}.json'), 'w') as f:
        json.dump(result, f, indent=4)

"""
Example usage:
    >>> python site_similarity/dataprep/alexa_scrapper.py --target_site "bradva.bg"
"""
